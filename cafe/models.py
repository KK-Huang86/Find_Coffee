from django.db import models
from django.db.models import F

from users.models import User


class CafeQuerySet(models.QuerySet):
    def work_friendly(self):
        """不限時且有插座（工作友善）"""
        return self.filter(
            limited_time__in=['maybe', 'no'],
            has_socket__in=['yes', 'maybe'],
        )

LIMITED_TIME_CHOICES = [
    ("no", "一律不限時"),
    ("maybe", "視情況限時"),
    ("yes", "一律限時"),
]
SOCKET_CHOICES = [
    ("no", "很少或沒有"),
    ("maybe", "視座位"),
    ("yes", "很多"),
]
PET_FRIENDLY_CHOICES = [
    ("no", "不歡迎寵物"),
    ("maybe", "視情況"),
    ("yes", "歡迎寵物"),
]
HAS_PET_CHOICES = [
    ("no", "沒有"),
    ("maybe", "有時有"),
    ("yes", "有貓貓狗狗"),
]


# Create your models here.

class Cafe(models.Model):
    objects = CafeQuerySet.as_manager()

    # 唯一key
    place_id = models.CharField(max_length=100, unique=True, db_index=True, verbose_name='Google Place ID')

    # 基本資料
    name = models.CharField(max_length=200, verbose_name='咖啡店名稱')
    address = models.TextField(max_length=300, verbose_name='地址')
    phone = models.CharField(max_length=20, blank=True, null=True, verbose_name='電話')

    # 地理位置
    lat = models.DecimalField(max_digits=9, decimal_places=6, blank=True, null=True, verbose_name='緯度')
    lng = models.DecimalField(max_digits=10, decimal_places=6, blank=True, null=True, verbose_name='經度')

    rating = models.DecimalField(max_digits=2, decimal_places=1, blank=True, null=True, verbose_name='Google 評分')
    user_ratings_total = models.IntegerField(default=0, verbose_name='評論數量')

    # 營業資訊
    opening_hours = models.JSONField(default=list, blank=True, verbose_name='營業時間')
    reviews = models.JSONField(default=list, null=True, blank=True, verbose_name='用戶評論')
    website = models.URLField(blank=True, null=True, verbose_name='官方網站')
    google_maps = models.URLField(blank=True, null=True, verbose_name='Google Maps 連結')

    favorite_count = models.IntegerField(default=0, verbose_name='收藏數')

    # 查詢資料是否有定期更新，每30天會更新資料（使用者若有查詢該筆資料）
    last_refreshed = models.DateTimeField(null=True, blank=True, verbose_name='資料最後更新時間')

    # AI 生成的摘要，供描述搜尋比對用（見 cafe-description-search change）
    ai_summary = models.TextField(null=True, blank=True, verbose_name='AI 摘要')

    # 咖啡店的環境
    limited_time = models.CharField(max_length=10, null=True, blank=True, choices=LIMITED_TIME_CHOICES,
                                    verbose_name='是否限時')
    has_socket = models.CharField(max_length=10, null=True, blank=True, choices=SOCKET_CHOICES,
                                  verbose_name='是否有插座')
    attributes_last_calculated_at = models.DateTimeField(null=True)
    pet_friendly = models.CharField(max_length=10, null=True, blank=True, choices=PET_FRIENDLY_CHOICES,
                                    verbose_name='寵物友善')
    has_pet = models.CharField(max_length=10, null=True, blank=True, choices=HAS_PET_CHOICES,
                               verbose_name='店內有貓貓狗狗')

    # photo
    photo_reference = models.CharField(blank=True, default='', verbose_name='Google Photo Reference')
    photo_s3_url = models.URLField(blank=True, default='', verbose_name='S3 照片 URL')
    view_count = models.IntegerField(default=0, verbose_name='瀏覽次數')
    photo_updated_at = models.DateTimeField(null=True, blank=True, verbose_name='照片更新時間')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def increment_favorite_count(self):
        """增加收藏數"""
        Cafe.objects.filter(
            pk=self.pk
        ).update(
            favorite_count=F('favorite_count') + 1
        )
        self.refresh_from_db(fields=['favorite_count'])
        # 避免 race condition

    def decrement_favorite_count(self):
        """減少收藏數"""
        if self.favorite_count > 0:
            Cafe.objects.filter(
                pk=self.pk, favorite_count__gt=0
            ).update(
                favorite_count=F('favorite_count') - 1
            )
            self.refresh_from_db(fields=['favorite_count'])
            # 避免 race condition

    def to_dict(self):
        """轉換成 Flex Message 需要的格式"""
        return {
            'name': self.name,
            'address': self.address,
            'phone': self.phone,
            'rating': self.rating,
            'user_ratings_total': self.user_ratings_total,
            'place_id': self.place_id,
            'google_maps': self.google_maps,
            'website': self.website,
            'lat': float(self.lat) if self.lat is not None else None,
            'lng': float(self.lng) if self.lng is not None else None,
            'opening_hours': self.opening_hours,
            'reviews': self.reviews or [],
            'ai_summary': self.ai_summary,
            'limited_time': self.limited_time,
            'has_socket': self.has_socket,
            'pet_friendly': self.pet_friendly,
            'has_pet': self.has_pet,
            'photo_reference': self.photo_reference,
            'photo_s3_url': self.photo_s3_url,
            'last_refreshed': self.last_refreshed.isoformat() if self.last_refreshed else None,
            # last_refreshed本身是 Datetime 格式，沒辦法直接被 JSON序列化，因此使用 isoformat()，將 Datetime 格式改成  "2025-01-20T12:30:00"
        }

    @classmethod
    def get_popular_cafes(cls):
        return cls.objects.order_by('-favorite_count')[:10]

    def __str__(self):
        return self.name

    class Meta:
        db_table = 'cafes'
        verbose_name = '咖啡店'
        verbose_name_plural = '咖啡店列表'
        indexes = [
            models.Index(fields=['-favorite_count']),
            models.Index(fields=['lat', 'lng']),  # 地理位置搜尋
        ]


class Favorite(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='favorites', verbose_name='使用者')
    cafe = models.ForeignKey(Cafe, on_delete=models.CASCADE, related_name='favorited_by', verbose_name='咖啡店')

    note = models.TextField(blank=True, null=True, verbose_name='個人備註')
    is_public = models.BooleanField(default=False, verbose_name='公開收藏')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'favorites'
        verbose_name = '收藏'
        verbose_name_plural = '收藏列表'
        unique_together = ('user', 'cafe')


class CafeAttributeVote(models.Model):
    """咖啡店屬性投票記錄"""

    ATTRIBUTE_CHOICES = [
        ('socket', '插座'),
        ('limited_time', "限時"),
        ('cheap', '價格'),
        ('quiet', '安靜'),
        ('pet_friendly', '寵物友善'),
        ('has_pet', '有貓貓狗狗')
    ]

    VALUE_CHOICES = [
        ('yes', '是/有'),
        ('no', '否/無'),
        ('maybe', '可能/部分'),
        ('unknown', '未知'),
    ]

    SOURCE_CHOICES = [
        ('user', '使用者回報'),
        ('cafenomad', 'CafeNomad API'),
        ('google', 'Google Places API'),
        ('inferred', '系統推論'),
    ]
    cafe = models.ForeignKey(Cafe, on_delete=models.CASCADE, related_name='attribute_votes', verbose_name='咖啡店')
    user = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, verbose_name='回報使用者')

    attribute = models.CharField(max_length=20, choices=ATTRIBUTE_CHOICES, verbose_name='屬性類型')
    value = models.CharField(max_length=20, choices=VALUE_CHOICES, verbose_name='屬性值')
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, verbose_name='資料來源')

    created_at = models.DateTimeField(auto_now_add=True, verbose_name='建立時間')

    class Meta:
        verbose_name = '咖啡店屬性投票'
        verbose_name_plural = '咖啡店屬性投票'

        indexes = [
            models.Index(fields=['cafe', 'attribute']),
            models.Index(fields=['created_at']),
        ]

        ordering = ['-created_at']

        # 防止別人瘋狂評分、評價
        constraints = [
            models.UniqueConstraint(
                fields=['cafe', 'attribute', 'user'],
                condition=models.Q(user__isnull=False),
                name='unique_user_vote_per_attribute'
            )
        ]

    def __str__(self):
        return f'{self.cafe.name} - {self.get_attribute_display()}: {self.get_value_display()}'


class CafeNomadCache(models.Model):
    """CafeNomad API 資料快取表"""

    STANDING_DESK_CHOICES = [
        ('yes', '有'),
        ('no', '無'),
    ]

    # 基本資訊
    cafenomad_id = models.CharField(max_length=100, unique=True, db_index=True, verbose_name='CafeNomad ID')
    name = models.CharField(max_length=200, verbose_name='店名')
    city = models.CharField(max_length=50, blank=True, verbose_name='城市')
    address = models.CharField(max_length=255, blank=True, verbose_name='地址')

    # 地理位置
    lat = models.DecimalField(max_digits=9, decimal_places=6, verbose_name='緯度')
    lng = models.DecimalField(max_digits=10, decimal_places=6, verbose_name='經度')

    # 咖啡店屬性
    socket = models.CharField(max_length=10, null=True, blank=True, choices=SOCKET_CHOICES, verbose_name='插座')
    limited_time = models.CharField(max_length=10, null=True, blank=True, choices=LIMITED_TIME_CHOICES,
                                    verbose_name='是否限時')
    standing_desk = models.CharField(max_length=10, null=True, blank=True, choices=STANDING_DESK_CHOICES,
                                     verbose_name='站立桌')

    # 評分資訊
    wifi = models.FloatField(null=True, blank=True, verbose_name='WiFi 穩定度')
    seat = models.FloatField(null=True, blank=True, verbose_name='通常有位')
    quiet = models.FloatField(null=True, blank=True, verbose_name='安靜程度')
    tasty = models.FloatField(null=True, blank=True, verbose_name='咖啡好喝')
    cheap = models.FloatField(null=True, blank=True, verbose_name='價格便宜')
    music = models.FloatField(null=True, blank=True, verbose_name='裝潢音樂')

    # 其他資訊
    url = models.URLField(max_length=500, blank=True, verbose_name='官方網站')
    mrt = models.CharField(max_length=100, blank=True, verbose_name='最近捷運站')
    open_time = models.CharField(max_length=200, blank=True, verbose_name='營業時間')

    synced_at = models.DateTimeField(auto_now=True, verbose_name='最後同步時間')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='建立時間')

    class Meta:
        db_table = 'cafe_nomad_cache'
        verbose_name = 'CafeNomad 快取'
        verbose_name_plural = 'CafeNomad 快取'

        # 關鍵索引（加速經緯度查詢）
        indexes = [
            models.Index(fields=['lat', 'lng'], name='idx_cafenomad_coords'),
            models.Index(fields=['city'], name='idx_cafenomad_city'),
            models.Index(fields=['synced_at'], name='idx_cafenomad_synced'),
        ]

        ordering = ['-synced_at']

    def __str__(self):
        return f"{self.name} ({self.city})"

    @property
    def has_complete_attributes(self):
        """是否有完整的屬性資訊"""
        return self.socket is not None and self.limited_time is not None

    def to_dict(self):
        return {
            'name': self.name,
            'city': self.city,
            'lat': float(self.lat),
            'lng': float(self.lng),
            'socket': self.socket,
            'limited_time': self.limited_time,
            'address': self.address,
        }
