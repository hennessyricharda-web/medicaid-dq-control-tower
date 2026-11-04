-- One row per member/program: the count and span of eligibility segments seen in the released view.
select msis_id, program_code, count(*) as segment_count, min(eff_start) as earliest_eff_start,
       max(coalesce(eff_end, date '9999-12-31')) as latest_eff_end
from {{ ref('stg_eligibility') }}
group by 1, 2
