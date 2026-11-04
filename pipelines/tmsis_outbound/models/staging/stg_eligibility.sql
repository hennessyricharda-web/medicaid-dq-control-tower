-- Staged eligibility segments, read ONLY from the RELEASED view (never bronze.ie_segment directly).
select person_source_key, msis_id, program_code, segment_type, eff_start, eff_end, _dq_batch_id
from {{ source('released', 'ie_segment_released') }}
