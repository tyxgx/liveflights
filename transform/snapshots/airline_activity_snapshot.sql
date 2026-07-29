{% snapshot airline_activity_snapshot %}

{{
    config(
        target_schema='analytics',
        unique_key='airline',
        strategy='check',
        check_cols=['flight_count', 'avg_speed_kmh', 'avg_altitude_ft'],
    )
}}

select * from {{ source('gold', 'airline_activity') }}

{% endsnapshot %}
