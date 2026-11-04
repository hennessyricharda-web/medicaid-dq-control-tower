-- Staged encounter headers, read ONLY from the RELEASED view (never bronze.encounter_header directly).
select chain_root, submitter_id, encounter_id, member_id, billing_npi, freq_code, total_charge, mco_paid,
       _dq_batch_id
from {{ source('released', 'encounter_header_released') }}
