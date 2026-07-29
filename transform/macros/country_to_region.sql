{#
  Coarse country -> region bucketing for reporting. Deliberately simple
  (business-level grouping for the region mart, not a re-derivation of the
  lat/lon-based regioning Spark already does in silver) — covers the
  countries that actually show up under the default Europe/US bounding
  boxes, falls back to "Other".
#}
{% macro country_to_region(country_column) -%}
    case
        when {{ country_column }} in (
            'United Kingdom', 'Ireland', 'France', 'Germany', 'Netherlands',
            'Belgium', 'Switzerland', 'Austria', 'Spain', 'Portugal', 'Italy',
            'Denmark', 'Sweden', 'Norway', 'Finland', 'Poland', 'Czechia',
            'Malta', 'Luxembourg'
        ) then 'Europe'
        when {{ country_column }} in (
            'United States', 'Canada', 'Mexico'
        ) then 'North America'
        when {{ country_column }} in (
            'India', 'Pakistan', 'Bangladesh', 'Sri Lanka', 'Nepal', 'Bhutan',
            'Maldives'
        ) then 'South Asia'
        else 'Other'
    end
{%- endmacro %}
