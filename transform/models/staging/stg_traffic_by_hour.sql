with source as (
    select * from {{ source('gold', 'traffic_by_hour') }}
)

select
    hour_bucket::timestamp                as hour_bucket,
    date_trunc('day', hour_bucket)::date  as traffic_date,
    flight_count::bigint                  as flight_count,
    avg_altitude_ft::double precision     as avg_altitude_ft,
    avg_speed_kmh::double precision       as avg_speed_kmh
from source
