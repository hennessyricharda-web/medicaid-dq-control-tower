-- One row per submitter: the encounter count and total charges seen in the released view.
select submitter_id, count(*) as encounter_count, sum(total_charge) as total_charge_sum,
       sum(mco_paid) as mco_paid_sum
from {{ ref('stg_encounters') }}
group by 1
