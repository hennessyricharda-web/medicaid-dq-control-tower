-- Medicaid (incl. MCHIP) eligibility spans: the CURRENT released version per grain key
-- (person, segment type, start, program), so a terminated or corrected span never survives
-- next to its replacement (ENG-W1-1).
select person_source_key, msis_id, program_code, segment_type, elig_group, eff_start,
       coalesce(eff_end, date '9999-12-31') as eff_end, county, household_id, recorded_at, _dq_batch_id
from {{ source('released', 'ie_segment_current') }}
where program_code in ('MEDICAID', 'MCHIP')
