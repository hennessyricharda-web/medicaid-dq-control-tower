with months as (
  -- reporting calendar comes from the state profile via dbt vars (no hard-coded year)
  select unnest(generate_series(cast('{{ var("calendar_start", "2026-01-01") }}' as date), cast('{{ var("calendar_end", "2026-12-01") }}' as date), interval 1 month))::date as month_start
)
select s.msis_id, s.program_code, m.month_start
from {{ ref('silver_eligibility_span') }} s
join months m on m.month_start between date_trunc('month', s.eff_start) and s.eff_end
group by 1, 2, 3
