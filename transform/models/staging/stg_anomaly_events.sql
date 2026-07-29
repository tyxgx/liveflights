with source as (
    select * from {{ source('gold', 'anomaly_events') }}
)

select
    icao24,
    callsign,
    trim(origin_country)                     as origin_country,
    ingest_ts::timestamp                      as ingest_ts,
    date_trunc('hour', ingest_ts)::timestamp  as anomaly_hour,
    latitude::double precision                as latitude,
    longitude::double precision               as longitude,
    altitude_ft::double precision             as altitude_ft,
    speed_kmh::double precision                as speed_kmh,
    anomaly_score::double precision           as anomaly_score,
    anomaly_type
from source
where icao24 is not null
