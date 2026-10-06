"""Arabic catalog labels for presentation; stored names and IDs stay canonical."""

DEFAULT_CATALOG_LABELS = {
    "latest_movies": "أحدث الأفلام",
    "top_movies": "الأفلام الأكثر شعبية",
    "latest_series": "أحدث المسلسلات",
    "top_series": "المسلسلات الأكثر شعبية",
}

AUTO_CATALOG_LABELS = {
    "auto_bollywood": "بوليوود",
    "auto_hollywood": "هوليوود",
    "auto_anime": "أنمي",
    "auto_kdrama": "الدراما الكورية",
    "auto_bengali": "المحتوى البنغالي",
    "auto_south_indian": "جنوب الهند",
    "auto_tamil": "التاميلية",
    "auto_telugu": "التيلوغوية",
    "auto_malayalam": "المالايالامية",
    "auto_kannada": "الكانادية",
    "auto_japanese": "اليابانية",
    "auto_korean": "الكورية",
    "auto_top_rated": "الأعلى تقييماً",
    "auto_recently_added": "أُضيف حديثاً",
    "auto_netflix": "نتفليكس",
    "auto_prime_video": "أمازون برايم",
    "auto_hotstar": "هوتستار",
    "auto_apple_tv": "آبل تي في",
    "auto_hulu": "هولو",
    "auto_hbo": "إتش بي أو",
    "auto_jiocinema": "جيو سينما",
    "auto_zee5": "زي 5",
    "auto_sonyliv": "سوني ليف",
    "auto_mx_player": "إم إكس بلاير",
    "auto_crunchyroll": "كرانشي رول",
}


def catalog_display_name(catalog: dict) -> str:
    """Translate known auto catalogs by stable key, preserving custom titles."""
    name = catalog.get("name") or "كاتلوج مخصص"
    if catalog.get("auto"):
        return AUTO_CATALOG_LABELS.get(catalog.get("auto_key"), name)
    return name
