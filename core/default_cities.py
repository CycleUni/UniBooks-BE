"""The cities each region starts with, and the seeding helper.

Taiwan uses its 22 counties and cities with their ISO 3166-2:TW codes. Hong
Kong is one city, so splitting by city would put every school in the same
group; it uses its three areas instead — Hong Kong Island, Kowloon and the
New Territories — which is what "nearby" means to a student there.

Each entry: code, English name, zh-TW name, zh-HK name.
"""

DEFAULT_CITIES = {
    'TW': [
        ('TPE', 'Taipei City', '臺北市', '台北市'),
        ('NWT', 'New Taipei City', '新北市', '新北市'),
        ('KEE', 'Keelung City', '基隆市', '基隆市'),
        ('TAO', 'Taoyuan City', '桃園市', '桃園市'),
        ('HSZ', 'Hsinchu City', '新竹市', '新竹市'),
        ('HSQ', 'Hsinchu County', '新竹縣', '新竹縣'),
        ('MIA', 'Miaoli County', '苗栗縣', '苗栗縣'),
        ('TXG', 'Taichung City', '臺中市', '台中市'),
        ('CHA', 'Changhua County', '彰化縣', '彰化縣'),
        ('NAN', 'Nantou County', '南投縣', '南投縣'),
        ('YUN', 'Yunlin County', '雲林縣', '雲林縣'),
        ('CYI', 'Chiayi City', '嘉義市', '嘉義市'),
        ('CYQ', 'Chiayi County', '嘉義縣', '嘉義縣'),
        ('TNN', 'Tainan City', '臺南市', '台南市'),
        ('KHH', 'Kaohsiung City', '高雄市', '高雄市'),
        ('PIF', 'Pingtung County', '屏東縣', '屏東縣'),
        ('ILA', 'Yilan County', '宜蘭縣', '宜蘭縣'),
        ('HUA', 'Hualien County', '花蓮縣', '花蓮縣'),
        ('TTT', 'Taitung County', '臺東縣', '台東縣'),
        ('PEN', 'Penghu County', '澎湖縣', '澎湖縣'),
        ('KIN', 'Kinmen County', '金門縣', '金門縣'),
        ('LIE', 'Lienchiang County', '連江縣', '連江縣'),
    ],
    'HK': [
        ('HKI', 'Hong Kong Island', '香港島', '港島'),
        ('KLN', 'Kowloon', '九龍', '九龍'),
        ('NT', 'New Territories', '新界', '新界'),
    ],
}


def seed_default_cities(region, city_model=None):
    """Give `region` its default cities if it has none at all.

    A region that already has cities is left alone, as with categories: an
    operator who edited them did so on purpose. Returns the number created.

    `city_model` lets a data migration pass its historical model.
    """
    if city_model is None:
        from core.models import City as city_model
    entries = DEFAULT_CITIES.get(region.code, [])
    if not entries or city_model.objects.filter(region=region).exists():
        return 0
    city_model.objects.bulk_create([
        city_model(
            region=region,
            code=code,
            name=name,
            translations={'zh-TW': {'name': zh_tw}, 'zh-HK': {'name': zh_hk}},
            sort_order=order,
        )
        for order, (code, name, zh_tw, zh_hk) in enumerate(entries, start=1)
    ])
    return len(entries)
