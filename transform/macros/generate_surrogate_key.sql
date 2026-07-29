{#
  Deterministic surrogate key from one or more columns, coalescing nulls to
  a placeholder so the hash is stable. Small local equivalent of
  dbt_utils.generate_surrogate_key, kept in-repo to avoid an external
  package dependency for a single macro.
#}
{% macro generate_surrogate_key(columns) -%}
    md5(
        {%- for col in columns %}
        coalesce(cast({{ col }} as varchar), '_dbt_null_')
        {%- if not loop.last %} || '||' || {% endif -%}
        {%- endfor %}
    )
{%- endmacro %}
