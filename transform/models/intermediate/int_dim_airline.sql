with airlines as (
    select distinct airline_name
    from {{ ref('stg_airline_activity') }}
)

select
    {{ generate_surrogate_key(['airline_name']) }} as airline_key,
    airline_name,
    case when airline_name = 'Unknown/Other' then true else false end as is_unclassified
from airlines
