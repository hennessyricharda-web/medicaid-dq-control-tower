-- Current version per (submitter, chain root). chain_root is resolved transitively at intake,
-- so E1 <- E2(7) <- E3(7) is ONE chain (ENG-W1-4). Precedence follows the contract's version
-- key (freq_code): a void (8) ends the chain, a replacement (7) supersedes the original (1);
-- arrival order (_dq_row_seq) only breaks ties within the same frequency (SCP-W1-6).
with ranked as (
  select *, row_number() over (
           partition by submitter_id, chain_root
           order by case freq_code when '8' then 3 when '7' then 2 else 1 end desc, _dq_row_seq desc) as rn
  from {{ source('released', 'encounter_header_released') }}
)
select chain_root as chain_id, submitter_id, encounter_id, member_id, billing_npi, freq_code,
       case when freq_code = '8' then 'VOID' else 'ACTIVE' end as chain_status,
       total_charge, mco_paid, pos, dx1, _dq_batch_id
from ranked where rn = 1
