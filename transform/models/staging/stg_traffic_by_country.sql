with source as (
    select * from {{ source('gold', 'traffic_by_country') }}
)

select
    trim(origin_country)               as origin_country,
    flight_count::bigint               as flight_count,
    avg_altitude_ft::double precision  as avg_altitude_ft,
    avg_speed_kmh::double precision    as avg_speed_kmh
from source
where origin_country is not null
