with countries as (
    select * from {{ ref('int_dim_country') }}
),

traffic as (
    select * from {{ ref('stg_traffic_by_country') }}
)

select
    countries.region,
    count(distinct countries.origin_country)          as num_countries,
    sum(traffic.flight_count)                          as total_flights,
    avg(traffic.avg_altitude_ft)                       as avg_altitude_ft,
    avg(traffic.avg_speed_kmh)                         as avg_speed_kmh
from traffic
inner join countries on traffic.origin_country = countries.origin_country
group by countries.region
order by total_flights desc
