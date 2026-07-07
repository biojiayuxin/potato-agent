create table if not exists networks (
  network_id text primary key,
  sample_count integer,
  input_genes_after_tpm_filter integer,
  genes_used_for_wgcna integer,
  soft_power integer,
  network_type text,
  tom_type text,
  correlation_method text,
  min_module_size integer,
  merge_cut_height double precision
);

create table if not exists genes (
  gene_id text primary key,
  gene_name text,
  chromosome text,
  start_pos integer,
  end_pos integer,
  annotation text
);

create table if not exists modules (
  network_id text references networks(network_id),
  module text,
  module_size integer,
  is_grey boolean,
  primary key (network_id, module)
);

create table if not exists network_genes (
  network_id text references networks(network_id),
  gene_id text references genes(gene_id),
  module text,
  variance_log2tpm double precision,
  kme_own_module double precision,
  is_grey boolean,
  primary key (network_id, gene_id)
);

create index if not exists idx_network_genes_gene
on network_genes(gene_id);

create index if not exists idx_network_genes_module
on network_genes(network_id, module);

create table if not exists network_gene_kme (
  network_id text references networks(network_id),
  gene_id text references genes(gene_id),
  module text,
  kme double precision,
  primary key (network_id, gene_id, module)
);

create index if not exists idx_kme_gene
on network_gene_kme(network_id, gene_id);

create table if not exists coexpression_edges_top (
  network_id text references networks(network_id),
  gene_id text references genes(gene_id),
  neighbor_gene_id text references genes(gene_id),
  tom double precision,
  tom_percentile double precision,
  rank integer,
  same_module boolean,
  gene_module text,
  neighbor_module text,
  primary key (network_id, gene_id, neighbor_gene_id)
);

create index if not exists idx_edges_query
on coexpression_edges_top(network_id, gene_id, rank);

create index if not exists idx_edges_tom
on coexpression_edges_top(network_id, gene_id, tom desc);

create index if not exists idx_edges_neighbor
on coexpression_edges_top(network_id, neighbor_gene_id);

create table if not exists module_overlaps (
  network_a text,
  module_a text,
  network_b text,
  module_b text,
  overlap_genes integer,
  size_a integer,
  size_b integer,
  jaccard double precision,
  overlap_ratio_a double precision,
  overlap_ratio_b double precision,
  p_value double precision,
  q_value double precision,
  primary key (network_a, module_a, network_b, module_b)
);

create index if not exists idx_module_overlaps_a
on module_overlaps(network_a, module_a);

create index if not exists idx_module_overlaps_b
on module_overlaps(network_b, module_b);

create table if not exists shared_coexpression_edges (
  gene_a text,
  gene_b text,
  n_networks integer,
  networks text[],
  tom_leaf double precision,
  tom_stem double precision,
  tom_root double precision,
  tom_reproductive double precision,
  tom_tuberization double precision,
  primary key (gene_a, gene_b)
);

create index if not exists idx_shared_edges_gene_a
on shared_coexpression_edges(gene_a);

create index if not exists idx_shared_edges_gene_b
on shared_coexpression_edges(gene_b);
