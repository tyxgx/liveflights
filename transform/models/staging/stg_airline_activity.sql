with source as (
    select * from {{ source('gold', 'airline_activity') }}
)

select
    trim(airline)                       as airline_name,
    flight_count::bigint                as flight_count,
    avg_speed_kmh::double precision     as avg_speed_kmh,
    avg_altitude_ft::double precision   as avg_altitude_ft
from source
where airline is not null
