from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List


@dataclass
class Property:
    id: str                      # e.g. "rightmove_12345678"
    source: str                  # rightmove | zoopla | onthemarket | openrent
    listing_type: str            # sale | rent
    url: str
    price: int                   # pcm for rent, total purchase price for sale
    bedrooms: int
    property_type: str           # flat | house | terraced | etc.
    address: str
    title: str = ""
    postcode: Optional[str] = None
    description: Optional[str] = None
    image_url: Optional[str] = None
    agent_name: Optional[str] = None
    # Enrichment fields (filled in by enricher.py)
    epc_rating: Optional[str] = None       # A–G
    crime_rate: Optional[str] = None       # Low | Medium | High
    nearest_school: Optional[str] = None
    school_rating: Optional[str] = None    # Outstanding | Good | Requires Improvement | Inadequate
    avg_sold_price: Optional[int] = None   # avg £/sqft or avg price in postcode
    commute_minutes: Optional[int] = None
    nearest_station: Optional[str] = None
    station_distance_miles: Optional[float] = None
    previous_price: Optional[int] = None  # set when a price drop is detected
    lat: Optional[float] = None           # from scraper (more reliable than postcode geocoding)
    lng: Optional[float] = None
    nearby_schools: Optional[str] = None  # JSON: [{name, rating, phase, urn}, ...]
    catchment_schools: Optional[str] = None  # JSON: [{name, phase, urn, in_catchment, council, source}, ...]
    first_seen: datetime = field(default_factory=datetime.now)


@dataclass
class Search:
    id: str
    name: str
    listing_type: str                      # sale | rent | both
    location: str = ""                     # e.g. "London", "Manchester", "E1" — plain English
    min_price: Optional[int] = None
    max_price: Optional[int] = None
    min_bedrooms: Optional[int] = None
    max_bedrooms: Optional[int] = None
    property_types: List[str] = field(default_factory=list)
    keywords_required: List[str] = field(default_factory=list)
    keywords_excluded: List[str] = field(default_factory=list)
    # Optional: explicit URLs override location-based search (advanced use only)
    rightmove_url: Optional[str] = None
    zoopla_url: Optional[str] = None
    onthemarket_url: Optional[str] = None
    openrent_url: Optional[str] = None
    savills_url: Optional[str] = None
    knightfrank_url: Optional[str] = None
    fineandcountry_url: Optional[str] = None
