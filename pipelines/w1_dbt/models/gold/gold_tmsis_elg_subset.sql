-- T-MSIS-shaped ELIGIBILITY subset (W1 illustration; the full ELG segments come in W2 with the crosswalk)
select msis_id as msis_identification_num, program_code, elig_group as eligibility_group,
       eff_start as enrollment_eff_date, eff_end as enrollment_end_date
from {{ ref('silver_eligibility_span') }}
