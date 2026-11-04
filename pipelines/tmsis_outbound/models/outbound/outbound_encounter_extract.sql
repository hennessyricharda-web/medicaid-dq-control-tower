-- Outbound T-MSIS-shaped encounter extract (demo illustration; the full crosswalk is a later package).
select chain_root as icn_orig, encounter_id as icn_adj, member_id as msis_identification_num,
       billing_npi as billing_prov_npi_num, total_charge as tot_bill_amt, mco_paid as tot_mco_paid_amt,
       submitter_id as plan_id_num
from {{ ref('stg_encounters') }}
