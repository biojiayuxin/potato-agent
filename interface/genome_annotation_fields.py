"""Public annotation field allowlists, matching the original five-genome release."""



GENE_FIELDS = ('assembly_id', 'gene_id', 'global_gene_key', 'gene_decision', 'total_transcript_count', 'valid_transcript_count', 'exception_transcript_count', 'distinct_protein_count', 'selected_tf_transcript_count', 'ambiguous_tf_transcript_count', 'unresolved_transcript_count', 'not_selected_transcript_count', 'has_selected_tf_isoform', 'all_valid_isoforms_selected', 'tf_family_union', 'tf_family_intersection', 'confidence_grades', 'ambiguous_family_union', 'isoform_presence_conflict', 'family_conflict')

PROTEIN_FIELDS = ('global_unique_protein_id', 'decision_status', 'is_tf_inclusive_result', 'selection_basis', 'tf_families', 'confidence_grades', 'unresolved_candidates', 'relevant_pfam_counts', 'has_any_interpro_hit', 'annotation_origin', 'protein_length', 'sequence_md5', 'sequence_sha256', 'decision_reason')

TRANSCRIPT_FIELDS = ('assembly_id', 'transcript_id', 'global_transcript_key', 'gene_id', 'global_gene_key', 'global_unique_protein_id', 'decision_status', 'is_tf_inclusive_result', 'selection_basis', 'tf_families', 'confidence_grades', 'unresolved_candidates', 'relevant_pfam_counts', 'annotation_origin', 'protein_length', 'sequence_md5', 'sequence_sha256')

EVIDENCE_FIELDS = ('global_unique_protein_id', 'signature_accession', 'signature_description', 'start', 'end', 'score', 'status', 'interpro_accession', 'interpro_description', 'rule_roles')

FAMILY_FIELDS = ('family', 'superfamily', 'public_rule_condition', 'coverage_status', 'confidence_grade', 'limitation', 'rule_source')

EXCEPTION_FIELDS = ('assembly_id', 'transcript_id', 'global_transcript_key', 'gene_id', 'gff_occurrences', 'inferred_from_cds', 'cds_features', 'protein_fasta_occurrences', 'protein_length', 'status', 'reason')

DOMAIN_DOWNLOAD_FIELDS = ('assembly_id', 'transcript_id', 'global_transcript_key', 'global_unique_protein_id', 'annotation_origin', 'sequence_md5', 'protein_length', 'analysis', 'signature_accession', 'signature_description', 'start', 'end', 'score', 'status', 'date', 'interpro_accession', 'interpro_description', 'go_terms')

NO_MATCH_DOWNLOAD_FIELDS = ('assembly_id', 'transcript_id', 'global_transcript_key', 'global_unique_protein_id', 'annotation_origin', 'sequence_md5', 'protein_length')

EXCEPTION_DOWNLOAD_FIELDS = ('assembly_id', 'transcript_id', 'global_transcript_key', 'status', 'reason')

ASSEMBLY_STATS_FIELDS = ('assembly_id', 'total_transcripts', 'valid_transcripts', 'hit_transcripts', 'no_hit_transcripts', 'extraction_exceptions', 'closure_status', 'total_genes', 'genes_with_valid_protein', 'exception_only_genes', 'selected_tf_transcripts', 'ambiguous_tf_transcripts', 'unresolved_transcripts', 'not_selected_transcripts', 'selected_tf_genes', 'isoform_presence_conflict_genes', 'family_conflict_genes', 'selected_unique_proteins_in_assembly', 'ambiguous_unique_proteins_in_assembly', 'unresolved_unique_proteins_in_assembly', 'homeobox_tf_candidate_transcripts', 'homeobox_tf_candidate_genes', 'homeobox_tf_candidate_unique_proteins')

PUBLIC_LIMITATIONS = ['PlantTFDB self-built HMM profiles are not publicly available in a local package.', 'PlantTFDB custom per-domain bit-score thresholds were not recoverable from the InterProScan TSV.', 'Grade A means the published domain-composition logic is complete, not identity to the official PlantTFDB server.', 'Grade B covers only a published Pfam branch of a rule that also permits a self-built profile.', 'Grade-C Homeobox TF candidate records are counted as TFs but are not assigned to an official PlantTFDB HB subfamily.', 'Eleven official families requiring self-built HMMs remain unavailable for family-level assignment.']

PUBLIC_SOURCES = {'family_rules': 'https://planttfdb.gao-lab.org/help_famschema.php', 'prediction_pipeline_paper': 'https://pmc.ncbi.nlm.nih.gov/articles/PMC3965000', 'threshold_method': 'https://planttfdb.gao-lab.org/help_domain_threshold.php'}
