from dataclasses import dataclass, field
from typing import Optional
import json


@dataclass
class Property:
    id: str                          # "rightmove:12345678"
    source: str                      # rightmove | zoopla | onthemarket | openrent
    listing_type: str                # sale | rent
    url: str
    title: str
    price: Optional[int]             # monthly PCM for rent, total for sale
    price_frequency: Optional[str]   # pcm | pw | None
    bedrooms: Optional[int]
    bathrooms: Optional[int]
    property_type: Optional[str]     # flat | terraced | detached | semi-detached | etc.
    address: Optional[str]
    postcode: Optional[str]
    latitude: Optional[float]
    longitude: Optional[float]
    description: Optional[str]
    features: list = field(default_factory=list)
    images: list = field(default_factory=list)
    agent_name: Optional[str] = None
    listed_date: Optional[str] = None
    first_seen: Optional[str] = None
    last_seen: Optional[str] = None
    status: str = "new"              # new | seen | saved | dismissed

    # Enrichment fields (populated after scraping)
    crime_score: Optional[str] = None
    crime_summary: Optional[str] = None
    epc_rating: Optional[str] = None
    avg_sold_price: Optional[int] = None
    commute_minutes: Optional[int] = None
    nearest_school: Optional[str] = None
    nearest_school_rating: Optional[str] = None

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        d["features"] = json.dumps(self.features)
        d["images"] = json.dumps(self.images)
        return d

    @classmethod
    def from_row(cls, row: dict) -> "Property":
        row = dict(row)
        row["features"] = json.loads(row.get("features") or "[]")
        row["images"] = json.loads(row.get("images") or "[]")
        return cls(**{k: v for k, v in row.items() if k in cls.__dataclass_fields__})


@dataclass
class SearchCriteria:
    id: str
    label: str
    listing_type: str                # sale | rent | both
    sources: list = field(default_factory=lambda: ["rightmove", "zoopla", "onthemarket", "openrent"])
    location: str = ""
    radius_miles: int = 1
    min_price: Optional[int] = None
    max_price: Optional[int] = None
    min_bedrooms: Optional[int] = None
    max_bedrooms: Optional[int] = None
    property_types: list = field(default_factory=list)  # empty = any
    keywords_require: list = field(default_factory=list)
    keywords_exclude: list = field(default_factory=list)
    commute_to: Optional[str] = None
    include_crime: bool = True
    include_epc: bool = True
    include_commute: bool = True
    include_schools: bool = True

    @classmethod
    def from_dict(cls, d: dict) -> "SearchCriteria":
        enrichment = d.pop("enrichment", {})
        return cls(
            id=d["id"],
            label=d.get("label", d["id"]),
            listing_type=d.get("type", "both"),
            sources=d.get("sources", ["rightmove", "zoopla", "onthemarket", "openrent"]),
            location=d.get("location", ""),
            radius_miles=d.get("radius_miles", 1),
            min_price=d.get("min_price"),
            max_price=d.get("max_price"),
            min_bedrooms=d.get("min_bedrooms"),
            max_bedrooms=d.get("max_bedrooms"),
            property_types=d.get("property_types", []),
            keywords_require=d.get("keywords_require", []),
            keywords_exclude=d.get("keywords_exclude", []),
            commute_to=enrichment.get("commute_to"),
            include_crime=enrichment.get("include_crime", True),
            include_epc=enrichment.get("include_epc", True),
            include_commute=bool(enrichment.get("commute_to")),
            include_schools=enrichment.get("include_schools", True),
        )
