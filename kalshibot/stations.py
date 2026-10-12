"""Kalshi daily-high series and the airport station each one settles on.

Kalshi's rules name a CLI code (e.g. CLINYC); the ICAO id is the matching
ASOS station, used for live METAR observations. `std_offset` is the UTC
offset of local *standard* time, because the climate day runs midnight to
midnight LST all year (1am-1am during daylight time).
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Station:
    series: str
    city: str
    icao: str
    lat: float
    lon: float
    std_offset: int


STATIONS: dict[str, Station] = {
    s.series: s
    for s in (
        Station("KXHIGHLAX", "Los Angeles", "KLAX", 33.9382, -118.3866, -8),
        Station("KXHIGHNY", "New York City", "KNYC", 40.7790, -73.9693, -5),
        Station("KXHIGHCHI", "Chicago", "KMDW", 41.7861, -87.7524, -6),
        Station("KXHIGHMIA", "Miami", "KMIA", 25.7881, -80.3169, -5),
        Station("KXHIGHAUS", "Austin", "KAUS", 30.1831, -97.6799, -6),
        Station("KXHIGHTDAL", "Dallas", "KDFW", 32.8998, -97.0403, -6),
        Station("KXHIGHTPHX", "Phoenix", "KPHX", 33.4342, -112.0116, -7),
        Station("KXHIGHPHIL", "Philadelphia", "KPHL", 39.8733, -75.2268, -5),
        Station("KXHIGHDEN", "Denver", "KDEN", 39.8466, -104.6562, -7),
        Station("KXHIGHTBOS", "Boston", "KBOS", 42.3606, -71.0097, -5),
        Station("KXHIGHTDC", "Washington DC", "KDCA", 38.8483, -77.0342, -5),
        Station("KXHIGHTMIN", "Minneapolis", "KMSP", 44.8831, -93.2289, -6),
        Station("KXHIGHTLV", "Las Vegas", "KLAS", 36.0719, -115.1634, -8),
        Station("KXHIGHTHOU", "Houston", "KHOU", 29.6375, -95.2825, -6),
        Station("KXHIGHTNOLA", "New Orleans", "KMSY", 29.9934, -90.2580, -6),
        Station("KXHIGHTSFO", "San Francisco", "KSFO", 37.6197, -122.3647, -8),
        Station("KXHIGHTSEA", "Seattle", "KSEA", 47.4447, -122.3144, -8),
        Station("KXHIGHTOKC", "Oklahoma City", "KOKC", 35.3889, -97.6006, -6),
        Station("KXHIGHTSATX", "San Antonio", "KSAT", 29.5443, -98.4839, -6),
    )
}
