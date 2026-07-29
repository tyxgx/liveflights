{#
  dbt's default behavior prefixes a custom schema with the profile's target
  schema (e.g. "analytics_staging"). We want the literal schema names
  already provisioned in Postgres (gold, staging, analytics), so override
  with the standard "custom schema replaces default" pattern.
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
