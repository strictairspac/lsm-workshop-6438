export const SELECTION_OUTCOMES={proven_optimal:{label:'已证最优',tone:'passed'},no_solution:{label:'无解',tone:'failed'},insufficient_evidence:{label:'证据不足',tone:'different'},queued:{label:'排队中',tone:''},running:{label:'演算中',tone:''}};
export const outcome=status=>SELECTION_OUTCOMES[status]||{label:status,tone:''};
