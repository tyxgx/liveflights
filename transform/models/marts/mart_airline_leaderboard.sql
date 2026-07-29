with airlines as (
    select * from {{ ref('int_dim_airline') }}
),

activity as (
    select * from {{ ref('stg_airline_activity') }}
)

select
    airlines.airline_key,
    activity.airline_name,
    airlines.is_unclassified,
    activity.flight_count,
    activity.avg_speed_kmh,
    activity.avg_altitude_ft,
    row_number() over (order by activity.flight_count desc) as activity_rank
from activity
inner join airlines on activity.airline_name = airlines.airline_name
order by activity_rank
