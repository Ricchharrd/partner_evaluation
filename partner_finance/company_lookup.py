"""Look up public company names before adding them to the watchlist."""
import re
import requests


API = "https://www.wikidata.org/w/api.php"


class CompanyLookupError(ValueError):
    pass


def _api(params):
    try:
        response = requests.get(API, params={**params, "format": "json"}, timeout=10,
            headers={"User-Agent": "PartnerIntelligence/1.0 (https://github.com/Ricchharrd/partner_evaluation; public lookup)",
                     "Accept": "application/json"})
        response.raise_for_status()
        if len(response.content) > 512_000:
            raise ValueError("Search response too large")
        return response.json()
    except (requests.RequestException, ValueError) as exc:
        raise CompanyLookupError("기업 정보 검색에 연결하지 못했습니다. 잠시 뒤 다시 시도해 주세요.") from exc


def _claim_value(entity, property_id):
    for claim in entity.get("claims", {}).get(property_id, []):
        value = claim.get("mainsnak", {}).get("datavalue", {}).get("value")
        if isinstance(value, dict):
            value = value.get("text")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def lookup_companies(name):
    query = " ".join(name.split())
    if len(query) < 2 or len(query) > 120:
        raise CompanyLookupError("기업명을 2~120자로 입력해 주세요.")
    language = "ko" if re.search(r"[가-힣]", query) else "en"
    result = _api({"action": "wbsearchentities", "search": query, "language": language,
                   "type": "item", "limit": 6})
    candidates = [item for item in result.get("search", [])
                  if re.fullmatch(r"Q\d+", item.get("id", ""))
                  and "disambiguation" not in item.get("description", "").casefold()]
    if not candidates:
        return []
    details = _api({"action": "wbgetentities", "ids": "|".join(item["id"] for item in candidates),
                    "props": "claims", "languages": "en|ko"}).get("entities", {})
    return [{"id": item["id"], "name": _claim_value(details.get(item["id"], {}), "P1448")
             or item.get("label", ""), "label": item.get("label", ""),
             "description": item.get("description", ""),
             "website": _claim_value(details.get(item["id"], {}), "P856"),
             "source": "https://www.wikidata.org/wiki/" + item["id"]}
            for item in candidates if item.get("label")]
