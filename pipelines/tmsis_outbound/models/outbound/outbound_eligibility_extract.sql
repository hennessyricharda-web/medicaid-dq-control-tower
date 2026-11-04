-- Outbound T-MSIS-shaped eligibility extract (demo illustration; the full crosswalk is a later package).
select person_source_key, msis_id, program_code, segment_type, eff_start, eff_end
from {{ ref('stg_eligibility') }}
