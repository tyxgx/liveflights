with countries as (
    select distinct origin_country
    from {{ ref('stg_traffic_by_country') }}
)

select
    {{ generate_surrogate_key(['origin_country']) }} as country_key,
    origin_country,
    {{ country_to_region('origin_country') }} as region
from countries
