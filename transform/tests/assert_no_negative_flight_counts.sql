-- Singular test: a dbt test query must return zero rows to pass.
-- flight_count is a countDistinct(icao24) in Spark, so it can never
-- legitimately be negative in any of these staging models.
select 'stg_traffic_by_hour' as model, flight_count
from {{ ref('stg_traffic_by_hour') }}
where flight_count < 0

union all

select 'stg_traffic_by_country' as model, flight_count
from {{ ref('stg_traffic_by_country') }}
where flight_count < 0

union all

select 'stg_airline_activity' as model, flight_count
from {{ ref('stg_airline_activity') }}
where flight_count < 0

union all

select 'stg_altitude_band_distribution' as model, flight_count
from {{ ref('stg_altitude_band_distribution') }}
where flight_count < 0
