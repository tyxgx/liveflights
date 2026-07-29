with anomalies as (
    select * from {{ ref('stg_anomaly_events') }}
),

countries as (
    select * from {{ ref('int_dim_country') }}
)

select
    anomalies.anomaly_type,
    coalesce(countries.region, 'Other')      as region,
    anomalies.anomaly_hour,
    count(*)                                 as anomaly_count,
    avg(anomalies.anomaly_score)             as avg_anomaly_score
from anomalies
left join countries on anomalies.origin_country = countries.origin_country
group by anomalies.anomaly_type, coalesce(countries.region, 'Other'), anomalies.anomaly_hour
order by anomalies.anomaly_hour desc, anomaly_count desc
