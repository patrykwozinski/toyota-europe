"""Translation strings for the trip analytics dashboard."""

TRANSLATIONS = {
    "en": {
        # Header
        "dashboard_subtitle": "Trip Analytics Dashboard",
        "hybrid": "Hybrid",
        "trips_recorded": "trips recorded",

        # Tab names
        "tab_overview": "Overview",
        "tab_fuel_ev": "Fuel &amp; EV",
        "tab_driving": "Driving",
        "tab_trips": "Trips",
        "tab_profile": "Profile",

        # KPI labels (row 1)
        "kpi_total_trips": "Total Trips",
        "kpi_total_distance": "Total Distance",
        "kpi_avg_fuel": "Avg Fuel Consumption",
        "kpi_electric_driving": "Electric Driving",
        "kpi_avg_score": "Avg Driving Score",
        "kpi_time_driving": "Time Driving",

        # KPI labels (row 2 - cost & environment)
        "kpi_total_fuel_cost": "Total Fuel Cost",
        "kpi_cost_per_km": "Cost per km",
        "kpi_total_fuel_used": "Total Fuel Used",
        "kpi_ev_distance": "EV Distance",
        "kpi_co2_emitted": "CO2 Emitted",
        "kpi_co2_saved": "CO2 Saved by EV",

        # KPI labels (row 3 - speed, highway, night)
        "kpi_avg_speed": "Avg Speed",
        "kpi_max_speed": "Max Speed",
        "kpi_highway_distance": "Highway Distance",
        "kpi_idle_time": "Idle Time",
        "kpi_night_trips": "Night Trips",
        "kpi_countries": "Countries",

        # Heatmap
        "heatmap_title": "Route Heatmap",
        "heatmap_desc_prefix": "trips overlaid",
        "heatmap_desc_suffix": "brighter = more frequent",
        "heatmap_waypoints": "waypoints",
        "heatmap_all": "All",
        "heatmap_ev": "EV",
        "heatmap_highway": "Highway",
        "heatmap_over_limit": "Over Limit",

        # Section titles - Overview
        "monthly_distance": "Monthly Distance",
        "monthly_fuel_cost": "Monthly Fuel Cost",
        "fuel_efficiency_trend": "Fuel Efficiency Trend (20-trip rolling avg, L/100km)",

        # Section titles - Fuel & EV
        "monthly_fuel_consumption": "Monthly Fuel Consumption (L/100km)",
        "ev_vs_fuel_distance": "Electric vs Fuel Distance by Month",
        "drive_mode_time": "Drive Mode Time (h)",
        "drive_mode_distance": "Drive Mode Distance (km)",
        "night_vs_day": "Night vs Day",
        "trip_categories_title": "Trip Categories",
        "ev_ratio_by_season": "EV Ratio by Season",
        "fuel_by_season": "Fuel by Season (L/100km)",

        # Section titles - Driving
        "max_speed_distribution": "Max Speed Distribution",
        "monthly_speed_trends": "Monthly Speed Trends",
        "highway_vs_city": "Highway vs City (km/month)",
        "idle_time_trend": "Idle Time Trend (%)",
        "monthly_driving_score": "Monthly Driving Score (Toyota app)",
        "driving_score_distribution": "Driving Score Distribution",
        "trips_by_weekday": "Trips by Day of Week",
        "trips_by_hour": "Trips by Hour of Day",

        # Section titles - Trips
        "longest_journeys": "Longest Journeys",
        "longest_journeys_desc": "consecutive trips with &le;45 min breaks merged into one journey",
        "service_history": "Service History",
        "odometer_tracking": "Odometer Tracking",

        # Section titles - Profile
        "driving_style_radar": "Driving Style Radar",
        "speed_profile_title": "Speed Profile (avg speed per trip)",
        "road_type_split": "Road Type Split",
        "trip_distance_distribution": "Trip Distance Distribution",
        "driving_habits": "Driving Habits",
        "engine_type_recommendation": "Engine Type Recommendation",

        # Table headers - Journeys
        "th_date": "Date",
        "th_distance_km": "Distance (km)",
        "th_drive_time": "Drive time",
        "th_total_time": "Total time",
        "th_stops": "Stops",
        "th_fuel_l": "Fuel (L)",
        "th_avg_l100km": "Avg L/100km",
        "th_cost": "Cost",
        "th_max_kmh": "Max km/h",

        # Table headers - Service
        "th_category": "Category",
        "th_provider": "Provider",
        "th_odometer_km": "Odometer (km)",
        "th_notes": "Notes",

        # Habit labels
        "habit_night_driving": "Night Driving",
        "habit_weekend_trips": "Weekend Trips",
        "habit_trips_per_day": "Trips / Day",
        "habit_peak_hour": "Peak Hour",

        # Road type labels
        "highway": "Highway",
        "city_other": "City / Other",

        # Engine recommendation
        "best_match": "Best Match",
        "runner_up": "Runner-up",
        "tradeoffs_label": "Trade-offs:",

        # Night/Day labels
        "night": "Night",
        "day": "Day",

        # Footer
        "footer_generated": "Generated",
        "footer_fuel_prices": "Fuel prices",

        # Weekday names (for compute_weekday_hour)
        "weekdays": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],

        # Trip categories (for compute_trip_categories)
        "cat_short": "Short (<10 km)",
        "cat_medium": "Medium (10-100)",
        "cat_long": "Long (>100 km)",

        # Seasons (for compute_seasonal)
        "season_winter": "Winter",
        "season_spring": "Spring",
        "season_summer": "Summer",
        "season_autumn": "Autumn",

        # Driving modes (for compute_driving_modes)
        "mode_electric": "Electric",
        "mode_eco": "Eco",
        "mode_power": "Power",
        "mode_charge": "Charge",

        # Radar labels (for compute_driving_profile)
        "radar_smoothness": "Smoothness",
        "radar_eco": "Eco-Consciousness",
        "radar_speed_discipline": "Speed Discipline",
        "radar_consistency": "Consistency",
        "radar_calmness": "Calmness",

        # Classification labels (for compute_driving_profile)
        "class_eco_expert": "Eco Expert",
        "class_eco_expert_desc": "You maximize electric driving, maintain smooth inputs, and respect speed limits. Your driving style prioritizes efficiency above all.",
        "class_spirited": "Spirited Driver",
        "class_spirited_desc": "You enjoy dynamic driving with frequent use of power mode and higher speeds. You prioritize engagement over efficiency.",
        "class_highway_warrior": "Highway Warrior",
        "class_highway_warrior_desc": "Most of your driving happens on highways at higher speeds. You cover long distances efficiently on motorways.",
        "class_city_navigator": "City Navigator",
        "class_city_navigator_desc": "Your trips are predominantly urban and short. You navigate city traffic frequently, ideal for electric and hybrid powertrains.",
        "class_smooth_cruiser": "Smooth Cruiser",
        "class_smooth_cruiser_desc": "You drive with consistent, smooth inputs and maintain a calm driving style. Your predictable driving is easy on passengers and the car.",
        "class_balanced": "Balanced Driver",
        "class_balanced_desc": "You have a well-rounded driving style that adapts to different conditions. A mix of city and highway driving with moderate efficiency.",
        "class_unknown": "Unknown",
        "class_unknown_desc": "Not enough data.",

        # Engine labels (for compute_engine_recommendation)
        "engine_bev": "Battery Electric",
        "engine_phev": "Plug-in Hybrid",
        "engine_hev": "Hybrid",
        "engine_petrol": "Petrol",
        "engine_diesel": "Diesel",

        # Engine reasons (dynamic, with {placeholders})
        "reason_bev_short_trips": "{pct:.0f}% of your trips are under 15 km — perfect for battery range",
        "reason_phev_short_trips": "{pct:.0f}% short trips can run on pure electric",
        "reason_bev_city": "{pct:.0f}% city driving maximizes regenerative braking",
        "reason_hev_city": "{pct:.0f}% city driving is where hybrids shine most",
        "reason_phev_city": "{pct:.0f}% city driving enables frequent EV mode",
        "reason_bev_ev_ready": "Already {pct:.0f}% EV driving shows readiness for full electric",
        "reason_phev_ev_usage": "Your {pct:.0f}% EV usage would increase with a larger battery",
        "reason_bev_eco_style": "Your eco-conscious style maximizes EV efficiency",
        "reason_hev_eco_style": "Your eco-conscious driving optimizes hybrid regeneration",
        "reason_diesel_long_trip": "Average trip of {km:.0f} km favors diesel efficiency at cruise",
        "reason_petrol_long_trip": "Your {km:.0f} km average trip suits petrol's highway comfort",
        "reason_diesel_highway": "{pct:.0f}% highway driving is diesel's sweet spot",
        "reason_petrol_highway": "{pct:.0f}% highway driving suits petrol turbo engines",
        "reason_petrol_spirited": "Your spirited driving style pairs well with responsive petrol engines",
        "reason_hev_fuel_savings": "At {fuel:.1f} L/100km, a hybrid could cut consumption by 20-30%",
        "reason_phev_fuel_savings": "At {fuel:.1f} L/100km, a PHEV could slash your fuel costs",

        # Engine fallback reasons
        "reason_bev_fallback_1": "Zero emissions and lowest running costs",
        "reason_bev_fallback_2": "Best for daily commutes and urban driving",
        "reason_phev_fallback_1": "Flexibility of electric for short trips with petrol backup",
        "reason_phev_fallback_2": "Good balance of efficiency and range",
        "reason_hev_fallback_1": "No charging needed with self-charging hybrid system",
        "reason_hev_fallback_2": "Great fuel efficiency in mixed driving",
        "reason_petrol_fallback_1": "Wide availability and lower purchase price",
        "reason_petrol_fallback_2": "Good for varied driving conditions",
        "reason_diesel_fallback_1": "Best highway fuel economy for long distances",
        "reason_diesel_fallback_2": "High torque for heavy loads",

        # Engine tradeoffs
        "tradeoff_bev": "Requires charging infrastructure; range limited on long highway trips",
        "tradeoff_phev": "Higher purchase price; needs regular charging to maximize savings",
        "tradeoff_hev": "Less electric range than PHEV/BEV; still burns fuel for all trips",
        "tradeoff_petrol": "Higher fuel costs; more CO2 emissions than electrified options",
        "tradeoff_diesel": "Higher emissions in city; declining resale value in some markets",

        # JS chart labels (embedded in const T)
        "js_distance_km": "Distance (km)",
        "js_trips": "Trips",
        "js_fuel_cost": "Fuel Cost",
        "js_fuel_price": "Fuel Price",
        "js_l100km": "L/100km",
        "js_electric_km": "Electric (km)",
        "js_fuel_km": "Fuel (km)",
        "js_electric_ratio_pct": "Electric Ratio (%)",
        "js_avg_score": "Avg Score",
        "js_avg_speed_kmh": "Avg Speed (km/h)",
        "js_max_speed_kmh": "Max Speed (km/h)",
        "js_highway_km": "Highway (km)",
        "js_city_km": "City (km)",
        "js_idle_pct": "Idle %",
        "js_l100km_rolling": "L/100km (20-trip rolling)",
        "js_odometer_km": "Odometer (km)",
        "js_your_profile": "Your Profile",
        "js_km": "km",
        "js_kmh": "km/h",
        "js_pct": "%",
        "js_score_range": "Score Range",
        "js_per_l": "/L",
    },
    "pl": {
        # Header
        "dashboard_subtitle": "Panel analityki podróży",
        "hybrid": "Hybryda",
        "trips_recorded": "zarejestrowanych podróży",

        # Tab names
        "tab_overview": "Przegląd",
        "tab_fuel_ev": "Paliwo &amp; EV",
        "tab_driving": "Jazda",
        "tab_trips": "Podróże",
        "tab_profile": "Profil",

        # KPI labels (row 1)
        "kpi_total_trips": "Liczba podróży",
        "kpi_total_distance": "Łączny dystans",
        "kpi_avg_fuel": "Średnie zużycie paliwa",
        "kpi_electric_driving": "Jazda elektryczna",
        "kpi_avg_score": "Średnia ocena jazdy",
        "kpi_time_driving": "Czas jazdy",

        # KPI labels (row 2 - cost & environment)
        "kpi_total_fuel_cost": "Łączny koszt paliwa",
        "kpi_cost_per_km": "Koszt za km",
        "kpi_total_fuel_used": "Zużycie paliwa",
        "kpi_ev_distance": "Dystans EV",
        "kpi_co2_emitted": "Emisja CO2",
        "kpi_co2_saved": "CO2 zaoszczędzone (EV)",

        # KPI labels (row 3 - speed, highway, night)
        "kpi_avg_speed": "Średnia prędkość",
        "kpi_max_speed": "Maks. prędkość",
        "kpi_highway_distance": "Dystans autostradowy",
        "kpi_idle_time": "Czas postoju",
        "kpi_night_trips": "Podróże nocne",
        "kpi_countries": "Kraje",

        # Heatmap
        "heatmap_title": "Mapa cieplna tras",
        "heatmap_desc_prefix": "podróży nałożonych",
        "heatmap_desc_suffix": "jaśniej = częściej",
        "heatmap_waypoints": "punktów trasy",
        "heatmap_all": "Wszystkie",
        "heatmap_ev": "EV",
        "heatmap_highway": "Autostrada",
        "heatmap_over_limit": "Przekroczenie",

        # Section titles - Overview
        "monthly_distance": "Miesięczny dystans",
        "monthly_fuel_cost": "Miesięczny koszt paliwa",
        "fuel_efficiency_trend": "Trend zużycia paliwa (średnia krocząca 20 podróży, L/100km)",

        # Section titles - Fuel & EV
        "monthly_fuel_consumption": "Miesięczne zużycie paliwa (L/100km)",
        "ev_vs_fuel_distance": "Dystans elektryczny vs paliwowy wg miesiąca",
        "drive_mode_time": "Czas trybów jazdy (h)",
        "drive_mode_distance": "Dystans trybów jazdy (km)",
        "night_vs_day": "Noc vs dzień",
        "trip_categories_title": "Kategorie podróży",
        "ev_ratio_by_season": "Udział EV wg pory roku",
        "fuel_by_season": "Paliwo wg pory roku (L/100km)",

        # Section titles - Driving
        "max_speed_distribution": "Rozkład prędkości maks.",
        "monthly_speed_trends": "Miesięczne trendy prędkości",
        "highway_vs_city": "Autostrada vs miasto (km/mies.)",
        "idle_time_trend": "Trend czasu postoju (%)",
        "monthly_driving_score": "Miesięczna ocena jazdy (aplikacja Toyota)",
        "driving_score_distribution": "Rozkład ocen jazdy",
        "trips_by_weekday": "Podróże wg dnia tygodnia",
        "trips_by_hour": "Podróże wg godziny",

        # Section titles - Trips
        "longest_journeys": "Najdłuższe podróże",
        "longest_journeys_desc": "kolejne podróże z przerwami &le;45 min połączone w jedną",
        "service_history": "Historia serwisowa",
        "odometer_tracking": "Przebieg pojazdu",

        # Section titles - Profile
        "driving_style_radar": "Radar stylu jazdy",
        "speed_profile_title": "Profil prędkości (śr. prędkość na podróży)",
        "road_type_split": "Podział typów dróg",
        "trip_distance_distribution": "Rozkład dystansów podróży",
        "driving_habits": "Nawyki jazdy",
        "engine_type_recommendation": "Rekomendacja typu silnika",

        # Table headers - Journeys
        "th_date": "Data",
        "th_distance_km": "Dystans (km)",
        "th_drive_time": "Czas jazdy",
        "th_total_time": "Czas całkowity",
        "th_stops": "Przystanki",
        "th_fuel_l": "Paliwo (L)",
        "th_avg_l100km": "Śr. L/100km",
        "th_cost": "Koszt",
        "th_max_kmh": "Maks. km/h",

        # Table headers - Service
        "th_category": "Kategoria",
        "th_provider": "Wykonawca",
        "th_odometer_km": "Przebieg (km)",
        "th_notes": "Uwagi",

        # Habit labels
        "habit_night_driving": "Jazda nocna",
        "habit_weekend_trips": "Podróże weekendowe",
        "habit_trips_per_day": "Podróży / dzień",
        "habit_peak_hour": "Godzina szczytu",

        # Road type labels
        "highway": "Autostrada",
        "city_other": "Miasto / inne",

        # Engine recommendation
        "best_match": "Najlepsze dopasowanie",
        "runner_up": "Druga opcja",
        "tradeoffs_label": "Kompromisy:",

        # Night/Day labels
        "night": "Noc",
        "day": "Dzień",

        # Footer
        "footer_generated": "Wygenerowano",
        "footer_fuel_prices": "Ceny paliw",

        # Weekday names
        "weekdays": ["Pon", "Wt", "Śr", "Czw", "Pt", "Sob", "Ndz"],

        # Trip categories
        "cat_short": "Krótkie (<10 km)",
        "cat_medium": "Średnie (10-100)",
        "cat_long": "Długie (>100 km)",

        # Seasons
        "season_winter": "Zima",
        "season_spring": "Wiosna",
        "season_summer": "Lato",
        "season_autumn": "Jesień",

        # Driving modes
        "mode_electric": "Elektryczny",
        "mode_eco": "Eco",
        "mode_power": "Power",
        "mode_charge": "Ładowanie",

        # Radar labels
        "radar_smoothness": "Płynność",
        "radar_eco": "Eko-świadomość",
        "radar_speed_discipline": "Dyscyplina prędkości",
        "radar_consistency": "Stabilność",
        "radar_calmness": "Spokój",

        # Classification labels
        "class_eco_expert": "Eko Ekspert",
        "class_eco_expert_desc": "Maksymalizujesz jazdę elektryczną, utrzymujesz płynne ruchy i przestrzegasz limitów prędkości. Twój styl jazdy stawia na efektywność ponad wszystko.",
        "class_spirited": "Dynamiczny kierowca",
        "class_spirited_desc": "Lubisz dynamiczną jazdę z częstym użyciem trybu power i wyższymi prędkościami. Stawiasz na zaangażowanie ponad efektywność.",
        "class_highway_warrior": "Autostradowiec",
        "class_highway_warrior_desc": "Większość Twojej jazdy odbywa się na autostradach z wyższą prędkością. Pokonujesz długie dystanse sprawnie po drogach szybkiego ruchu.",
        "class_city_navigator": "Miejski nawigator",
        "class_city_navigator_desc": "Twoje podróże są głównie miejskie i krótkie. Często poruszasz się w ruchu miejskim — idealnie do napędów elektrycznych i hybrydowych.",
        "class_smooth_cruiser": "Płynny podróżnik",
        "class_smooth_cruiser_desc": "Jedziesz ze stabilnymi, płynnymi ruchami i utrzymujesz spokojny styl jazdy. Twoja przewidywalna jazda jest komfortowa dla pasażerów i samochodu.",
        "class_balanced": "Zrównoważony kierowca",
        "class_balanced_desc": "Masz wszechstronny styl jazdy, który dostosowuje się do różnych warunków. Mieszanka jazdy miejskiej i autostradowej z umiarkowaną efektywnością.",
        "class_unknown": "Nieznany",
        "class_unknown_desc": "Za mało danych.",

        # Engine labels
        "engine_bev": "Elektryczny (BEV)",
        "engine_phev": "Plug-in Hybrid",
        "engine_hev": "Hybryda",
        "engine_petrol": "Benzyna",
        "engine_diesel": "Diesel",

        # Engine reasons (dynamic)
        "reason_bev_short_trips": "{pct:.0f}% Twoich podróży jest poniżej 15 km — idealnie dla zasięgu baterii",
        "reason_phev_short_trips": "{pct:.0f}% krótkich podróży może odbywać się na czystym prądzie",
        "reason_bev_city": "{pct:.0f}% jazdy miejskiej maksymalizuje rekuperację",
        "reason_hev_city": "{pct:.0f}% jazdy miejskiej to domena hybryd",
        "reason_phev_city": "{pct:.0f}% jazdy miejskiej umożliwia częsty tryb EV",
        "reason_bev_ev_ready": "Już {pct:.0f}% jazdy EV pokazuje gotowość na pełny elektryk",
        "reason_phev_ev_usage": "Twoje {pct:.0f}% jazdy EV wzrosłoby z większą baterią",
        "reason_bev_eco_style": "Twój ekologiczny styl jazdy maksymalizuje wydajność EV",
        "reason_hev_eco_style": "Twoja eko-jazda optymalizuje regenerację hybrydy",
        "reason_diesel_long_trip": "Średnia podróży {km:.0f} km sprzyja wydajności diesla na trasie",
        "reason_petrol_long_trip": "Twoja średnia {km:.0f} km na podróży pasuje do komfortu benzyny na trasie",
        "reason_diesel_highway": "{pct:.0f}% jazdy autostradowej to mocna strona diesla",
        "reason_petrol_highway": "{pct:.0f}% jazdy autostradowej pasuje do silników benzynowych turbo",
        "reason_petrol_spirited": "Twój dynamiczny styl jazdy świetnie pasuje do responsywnych silników benzynowych",
        "reason_hev_fuel_savings": "Przy {fuel:.1f} L/100km hybryda mogłaby zmniejszyć zużycie o 20-30%",
        "reason_phev_fuel_savings": "Przy {fuel:.1f} L/100km PHEV może drastycznie obniżyć koszty paliwa",

        # Engine fallback reasons
        "reason_bev_fallback_1": "Zerowa emisja i najniższe koszty eksploatacji",
        "reason_bev_fallback_2": "Najlepszy do codziennych dojazdów i jazdy miejskiej",
        "reason_phev_fallback_1": "Elastyczność elektryka na krótkie trasy z paliwowym zapasem",
        "reason_phev_fallback_2": "Dobra równowaga wydajności i zasięgu",
        "reason_hev_fallback_1": "Bez potrzeby ładowania dzięki samoładującemu się systemowi hybrydy",
        "reason_hev_fallback_2": "Świetna wydajność paliwowa w mieszanej jeździe",
        "reason_petrol_fallback_1": "Szeroka dostępność i niższa cena zakupu",
        "reason_petrol_fallback_2": "Dobry do różnych warunków jazdy",
        "reason_diesel_fallback_1": "Najlepsze spalanie na autostradzie przy długich trasach",
        "reason_diesel_fallback_2": "Wysoki moment obrotowy przy dużych obciążeniach",

        # Engine tradeoffs
        "tradeoff_bev": "Wymaga infrastruktury ładowania; ograniczony zasięg na długich trasach autostradowych",
        "tradeoff_phev": "Wyższa cena zakupu; wymaga regularnego ładowania dla maksymalnych oszczędności",
        "tradeoff_hev": "Mniejszy zasięg elektryczny niż PHEV/BEV; nadal spala paliwo na każdej podróży",
        "tradeoff_petrol": "Wyższe koszty paliwa; większa emisja CO2 niż opcje zelektryfikowane",
        "tradeoff_diesel": "Wyższa emisja w mieście; spadająca wartość odsprzedaży na niektórych rynkach",

        # JS chart labels
        "js_distance_km": "Dystans (km)",
        "js_trips": "Podróże",
        "js_fuel_cost": "Koszt paliwa",
        "js_fuel_price": "Cena paliwa",
        "js_l100km": "L/100km",
        "js_electric_km": "Elektryczny (km)",
        "js_fuel_km": "Paliwowy (km)",
        "js_electric_ratio_pct": "Udział elektryczny (%)",
        "js_avg_score": "Średnia ocena",
        "js_avg_speed_kmh": "Śr. prędkość (km/h)",
        "js_max_speed_kmh": "Maks. prędkość (km/h)",
        "js_highway_km": "Autostrada (km)",
        "js_city_km": "Miasto (km)",
        "js_idle_pct": "Postój %",
        "js_l100km_rolling": "L/100km (śr. krocząca 20 podróży)",
        "js_odometer_km": "Przebieg (km)",
        "js_your_profile": "Twój profil",
        "js_km": "km",
        "js_kmh": "km/h",
        "js_pct": "%",
        "js_score_range": "Zakres ocen",
        "js_per_l": "/L",
    },
}


def get_translations(lang: str) -> dict:
    """Return translation dict for the given language. Falls back to English."""
    return TRANSLATIONS.get(lang, TRANSLATIONS["en"])
