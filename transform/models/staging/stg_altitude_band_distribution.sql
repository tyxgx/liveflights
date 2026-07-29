with source as (
    select * from {{ source('gold', 'altitude_band_distribution') }}
)

select
    trim(altitude_band)              as altitude_band,
    flight_count::bigint             as flight_count,
    avg_speed_kmh::double precision  as avg_speed_kmh
from source
