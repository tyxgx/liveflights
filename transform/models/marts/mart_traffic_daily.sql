with daily as (
    select
        traffic_date,
        sum(flight_count)      as total_flights,
        avg(avg_altitude_ft)   as avg_altitude_ft,
        avg(avg_speed_kmh)     as avg_speed_kmh
    from {{ ref('stg_traffic_by_hour') }}
    group by traffic_date
),

with_prior_day as (
    select
        *,
        lag(total_flights) over (order by traffic_date) as prior_day_flights
    from daily
)

select
    traffic_date,
    total_flights,
    avg_altitude_ft,
    avg_speed_kmh,
    prior_day_flights,
    {{ pct_change('total_flights', 'prior_day_flights') }} as day_over_day_change_pct
from with_prior_day
order by traffic_date
