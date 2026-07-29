{#
  Percentage change from `old_value` to `new_value`, null-safe (returns
  null rather than dividing by zero when old_value is 0 or null).
#}
{% macro pct_change(new_value, old_value) -%}
    case
        when {{ old_value }} is null or {{ old_value }} = 0 then null
        else round((({{ new_value }} - {{ old_value }})::numeric / {{ old_value }}) * 100, 2)
    end
{%- endmacro %}
