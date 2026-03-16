"""Country/currency registry, fuel price scraping, and exchange rate fetching."""

import json
import re
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

CACHE_PATH = Path(__file__).parent / "fuel_prices.json"

# ISO country code -> info
COUNTRY_INFO = {
    "PL": {"name": "Poland", "currency": "PLN", "symbol": "zl", "tz": "Europe/Warsaw"},
    "DE": {"name": "Germany", "currency": "EUR", "symbol": "\u20ac", "tz": "Europe/Berlin"},
    "FR": {"name": "France", "currency": "EUR", "symbol": "\u20ac", "tz": "Europe/Paris"},
    "IT": {"name": "Italy", "currency": "EUR", "symbol": "\u20ac", "tz": "Europe/Rome"},
    "ES": {"name": "Spain", "currency": "EUR", "symbol": "\u20ac", "tz": "Europe/Madrid"},
    "NL": {"name": "Netherlands", "currency": "EUR", "symbol": "\u20ac", "tz": "Europe/Amsterdam"},
    "BE": {"name": "Belgium", "currency": "EUR", "symbol": "\u20ac", "tz": "Europe/Brussels"},
    "AT": {"name": "Austria", "currency": "EUR", "symbol": "\u20ac", "tz": "Europe/Vienna"},
    "PT": {"name": "Portugal", "currency": "EUR", "symbol": "\u20ac", "tz": "Europe/Lisbon"},
    "IE": {"name": "Ireland", "currency": "EUR", "symbol": "\u20ac", "tz": "Europe/Dublin"},
    "FI": {"name": "Finland", "currency": "EUR", "symbol": "\u20ac", "tz": "Europe/Helsinki"},
    "GR": {"name": "Greece", "currency": "EUR", "symbol": "\u20ac", "tz": "Europe/Athens"},
    "SK": {"name": "Slovakia", "currency": "EUR", "symbol": "\u20ac", "tz": "Europe/Bratislava"},
    "SI": {"name": "Slovenia", "currency": "EUR", "symbol": "\u20ac", "tz": "Europe/Ljubljana"},
    "EE": {"name": "Estonia", "currency": "EUR", "symbol": "\u20ac", "tz": "Europe/Tallinn"},
    "LV": {"name": "Latvia", "currency": "EUR", "symbol": "\u20ac", "tz": "Europe/Riga"},
    "LT": {"name": "Lithuania", "currency": "EUR", "symbol": "\u20ac", "tz": "Europe/Vilnius"},
    "CZ": {"name": "Czech_Republic", "currency": "CZK", "symbol": "K\u010d", "tz": "Europe/Prague"},
    "SE": {"name": "Sweden", "currency": "SEK", "symbol": "kr", "tz": "Europe/Stockholm"},
    "DK": {"name": "Denmark", "currency": "DKK", "symbol": "kr", "tz": "Europe/Copenhagen"},
    "NO": {"name": "Norway", "currency": "NOK", "symbol": "kr", "tz": "Europe/Oslo"},
    "CH": {"name": "Switzerland", "currency": "CHF", "symbol": "CHF", "tz": "Europe/Zurich"},
    "HU": {"name": "Hungary", "currency": "HUF", "symbol": "Ft", "tz": "Europe/Budapest"},
    "RO": {"name": "Romania", "currency": "RON", "symbol": "lei", "tz": "Europe/Bucharest"},
    "BG": {"name": "Bulgaria", "currency": "BGN", "symbol": "лв", "tz": "Europe/Sofia"},
    "HR": {"name": "Croatia", "currency": "EUR", "symbol": "\u20ac", "tz": "Europe/Zagreb"},
    "GB": {"name": "United_Kingdom", "currency": "GBP", "symbol": "\u00a3", "tz": "Europe/London"},
}

# Fuel type -> GlobalPetrolPrices URL segment
FUEL_TYPE_URLS = {
    "gasoline": "gasoline_prices",
    "diesel": "diesel_prices",
    "lpg": "lpg_prices",
}

# Hardcoded PL gasoline prices for seeding/fallback
LEGACY_PL_PRICES = {
    "2025-03": 5.96, "2025-04": 5.92, "2025-05": 5.74,
    "2025-06": 6.01, "2025-07": 5.90, "2025-08": 5.80,
    "2025-09": 5.82, "2025-10": 5.84, "2025-11": 5.80,
    "2025-12": 5.73, "2026-01": 5.59, "2026-02": 5.71,
    "2026-03": 6.19,
}

FUEL_PRICE_DEFAULT = 5.90


def get_country_info(code: str) -> dict:
    code = code.upper()
    if code not in COUNTRY_INFO:
        supported = ", ".join(sorted(COUNTRY_INFO.keys()))
        print(f"Unknown country code '{code}'. Supported: {supported}", file=sys.stderr)
        raise SystemExit(1)
    return COUNTRY_INFO[code]


def load_cache() -> dict:
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"prices": {}, "exchange_rates": {}}


def save_cache(cache: dict) -> None:
    try:
        CACHE_PATH.write_text(json.dumps(cache, indent=2, ensure_ascii=False))
    except OSError as e:
        print(f"Warning: could not save fuel price cache: {e}", file=sys.stderr)


def scrape_fuel_price(country_code: str, fuel_type: str = "gasoline") -> float | None:
    """Scrape current fuel price per liter from GlobalPetrolPrices.com."""
    info = COUNTRY_INFO.get(country_code.upper())
    if not info:
        return None

    url_segment = FUEL_TYPE_URLS.get(fuel_type, "gasoline_prices")
    country_name = info["name"]
    url = f"https://www.globalpetrolprices.com/{country_name}/{url_segment}/"

    currency = info["currency"]
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; TripDashboard/1.0)",
            "Accept": "text/html",
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="replace")

        # Try to extract the current local currency price from prose:
        # "The current gasoline price in Poland is PLN 5.84 per liter"
        pattern = rf'current.*?{re.escape(currency)}\s+(\d+[.,]\d{{2,3}})\s+per\s+liter'
        m = re.search(pattern, html, re.IGNORECASE)
        if m:
            price = float(m.group(1).replace(",", "."))
            if 0.01 < price < 500:
                return price

        # Fallback: look in the table — second numeric td is usually local currency
        td_matches = re.findall(r'<td[^>]*>\s*(\d+[.,]\d{2,3})\s*</td>', html)
        if len(td_matches) >= 2:
            price = float(td_matches[1].replace(",", "."))
            if 0.01 < price < 500:
                return price
    except Exception as e:
        print(f"Warning: failed to scrape fuel price for {country_code}/{fuel_type}: {e}", file=sys.stderr)

    return None


def fetch_exchange_rate(from_currency: str, to_currency: str) -> float | None:
    """Fetch exchange rate from open.er-api.com (no API key needed)."""
    if from_currency == to_currency:
        return 1.0

    url = f"https://open.er-api.com/v6/latest/{from_currency}"
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; TripDashboard/1.0)",
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if data.get("result") == "success" and to_currency in data.get("rates", {}):
            return data["rates"][to_currency]
    except Exception as e:
        print(f"Warning: failed to fetch exchange rate {from_currency}->{to_currency}: {e}", file=sys.stderr)

    return None


def get_exchange_rate(from_currency: str, to_currency: str, cache: dict) -> float:
    """Get exchange rate with cache fallback. Returns 1.0 as last resort."""
    if from_currency == to_currency:
        return 1.0

    cache_key = f"{from_currency}_{to_currency}"
    rates = cache.setdefault("exchange_rates", {})

    # Check if cached rate is fresh (< 24h)
    fetched_at = rates.get("fetched_at", "")
    if fetched_at:
        try:
            age = (datetime.now() - datetime.fromisoformat(fetched_at)).total_seconds()
            if age < 86400 and cache_key in rates:
                return rates[cache_key]
        except (ValueError, TypeError):
            pass

    # Fetch fresh rate
    rate = fetch_exchange_rate(from_currency, to_currency)
    if rate is not None:
        rates[cache_key] = rate
        rates["fetched_at"] = datetime.now().isoformat()
        save_cache(cache)
        return rate

    # Fall back to cached (even if stale)
    if cache_key in rates:
        print(f"Warning: using stale exchange rate for {cache_key}", file=sys.stderr)
        return rates[cache_key]

    print(f"Warning: no exchange rate for {from_currency}->{to_currency}, using 1.0", file=sys.stderr)
    return 1.0


def get_fuel_price(country: str, month: str, fuel_type: str,
                   conn=None, cache: dict | None = None) -> float:
    """Get fuel price per liter for a country/month/fuel_type.

    Fallback chain: DB -> scrape -> JSON cache -> legacy PL dict -> default.
    """
    country = country.upper()

    # 1. Try DB
    if conn is not None:
        try:
            row = conn.execute(
                "SELECT price FROM fuel_prices WHERE country=? AND month=? AND fuel_type=?",
                (country, month, fuel_type),
            ).fetchone()
            if row:
                return row[0]
        except Exception:
            pass

    # 2. Try JSON cache
    if cache is not None:
        cached = cache.get("prices", {}).get(country, {}).get(fuel_type, {}).get(month)
        if cached is not None:
            return cached

    # 3. Try scraping (for current month only — historical prices aren't scrapeable)
    current_month = datetime.now().strftime("%Y-%m")
    if month == current_month:
        price = scrape_fuel_price(country, fuel_type)
        if price is not None:
            # Store in DB
            if conn is not None:
                currency = COUNTRY_INFO.get(country, {}).get("currency", "???")
                try:
                    conn.execute(
                        "INSERT OR REPLACE INTO fuel_prices (country, month, fuel_type, currency, price, source) "
                        "VALUES (?, ?, ?, ?, ?, 'scraped')",
                        (country, month, fuel_type, currency, price),
                    )
                    conn.commit()
                except Exception:
                    pass
            # Store in cache
            if cache is not None:
                cache.setdefault("prices", {}).setdefault(country, {}).setdefault(fuel_type, {})[month] = price
                save_cache(cache)
            return price

    # 4. Try most recent known price for this country/fuel_type
    if conn is not None:
        try:
            row = conn.execute(
                "SELECT price FROM fuel_prices WHERE country=? AND fuel_type=? ORDER BY month DESC LIMIT 1",
                (country, fuel_type),
            ).fetchone()
            if row:
                return row[0]
        except Exception:
            pass

    # 5. Legacy PL fallback
    if country == "PL" and fuel_type == "gasoline":
        return LEGACY_PL_PRICES.get(month, FUEL_PRICE_DEFAULT)

    # 6. Default
    return FUEL_PRICE_DEFAULT


def seed_pl_prices(conn) -> None:
    """Seed the fuel_prices table with hardcoded PL gasoline prices."""
    for month, price in LEGACY_PL_PRICES.items():
        conn.execute(
            "INSERT OR IGNORE INTO fuel_prices (country, month, fuel_type, currency, price, source) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("PL", month, "gasoline", "PLN", price, "hardcoded"),
        )
    conn.commit()
