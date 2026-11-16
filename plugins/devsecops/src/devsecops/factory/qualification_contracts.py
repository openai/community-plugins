"""Closed qualification and video contracts; assertions are not live authority."""
from .contracts import DIGEST, array, document, object_schema

UTC = {'type': 'string', 'format': 'date-time'}
LABEL = {'type': 'string', 'minLength': 1, 'maxLength': 160}
RELATIVE = {'type': 'string', 'minLength': 1, 'maxLength': 4096,
            'pattern': r'^(?!/)(?!.*(?:^|/)\.\.(?:/|$))[^\u0000\r\n\\]+$'}
REFERENCE = object_schema({'path': RELATIVE, 'sha256': DIGEST,
                           'bytes': {'type': 'integer', 'minimum': 1, 'maximum': 1_000_000_000_000}})
IDENTITIES = object_schema({key + '_sha256': DIGEST for key in
                           ('scenario', 'assertions', 'config', 'lock', 'package', 'environment')})
DECISION = {'enum': ['pass', 'fail', 'incomplete']}
SECONDS = {'type': 'number', 'minimum': 0, 'maximum': 2_592_000}
RANGE = object_schema({'start_seconds': SECONDS, 'end_seconds': SECONDS})
HANDOFFS = ('source-to-ci', 'ci-registry-deployment', 'deployment-to-argo',
            'argo-to-runtime', 'fault-correction-recovery')
HANDOFF_REQUIREMENTS = {
    'source-to-ci': ({'build-promote'}, {'source-commit', 'pipeline-id', 'job-id'}),
    'ci-registry-deployment': ({'build-promote', 'primary-deliver'},
                              {'mission-image-digest', 'nginx-image-digest', 'sbom-subject',
                               'signature-subject', 'deployment-commit'}),
    'deployment-to-argo': ({'primary-deliver', 'primary-probes'},
                         {'deployment-commit', 'argo-revision', 'application'}),
    'argo-to-runtime': ({'primary-baseline', 'primary-probes'},
                       {'cluster-uid', 'namespace', 'workload-uid', 'runtime-image-digest',
                        'http-probe', 'metrics-query', 'isolation-probe'}),
    'fault-correction-recovery': ({'primary-confirm-fault', 'primary-correction', 'primary-deliver',
                                  'primary-recovery', 'reset', 'replay-verify', 'cleanup'},
                                 {'fault-commit', 'correction-patch-sha256', 'deployment-commit',
                                  'recovery-evidence', 'reset-commit', 'cleanup-evidence'}),
}
CLAIM_ROLES = sorted(set().union(*(roles for _, roles in HANDOFF_REQUIREMENTS.values())))
CLAIM = object_schema({'artifact': RELATIVE, 'json_pointer': {'type': 'string', 'maxLength': 2048},
                       'role': {'enum': CLAIM_ROLES}, 'label': LABEL,
                       'value': {'type': ['string', 'number', 'boolean', 'null']}})

CHAPTERS_SCHEMA = document('video-chapters', {
    'schema_version': {'const': 1}, 'kind': {'const': 'factory-video-chapters'},
    'run_id': LABEL, 'duration_seconds': {'type': 'number', 'exclusiveMinimum': 0, 'maximum': 2_592_000},
    'chapters': array(object_schema({
        'id': LABEL, 'title': LABEL, 'phase_id': {'type': ['string', 'null'], 'maxLength': 160},
        'attempt': {'enum': ['shared', 'primary', 'replay']},
        'source_range': RANGE, 'observed_at': UTC, 'evidence_paths': array(RELATIVE, maximum=200),
    }), minimum=35, maximum=35),
    'handoffs': array(object_schema({'kind': {'enum': list(HANDOFFS)}, 'source_range': RANGE,
                                   'phase_ids': array(LABEL, minimum=1, maximum=33),
                                   'claims': array(CLAIM, minimum=1, maximum=100)}), minimum=5, maximum=5),
})

VIDEO_REVIEW_SCHEMA = document('video-review', {
    'schema_version': {'const': 1}, 'kind': {'const': 'factory-video-review'},
    'run_id': LABEL, 'profile': {'const': 'bigbang-live'}, 'environment_id': LABEL,
    'identities': IDENTITIES, 'native_identity_sha256': DIGEST,
    'requested_client_model': {'oneOf': [object_schema({'client_version': LABEL, 'client_sha256': DIGEST,
                                            'model': LABEL, 'reasoning_effort': LABEL}), {'type': 'null'}]},
    'result': REFERENCE, 'evidence_manifest': REFERENCE,
    'video': REFERENCE, 'chapters': REFERENCE, 'transcript': REFERENCE,
    'video_format': {'enum': ['mp4', 'webm', 'mov', 'mkv']},
    'transcript_format': {'enum': ['webvtt', 'srt']},
    'duration_seconds': {'type': 'number', 'exclusiveMinimum': 0, 'maximum': 2_592_000},
    'capture_started_at': UTC, 'capture_ended_at': UTC, 'recording_date': UTC,
    'reviewer': LABEL, 'reviewed_at': UTC,
    'reviewer_did_not_build_demo': {'type': 'boolean'}, 'watched_full_delivered_video': {'type': 'boolean'},
    'playback_decision': DECISION, 'completeness_decision': DECISION, 'clarity_decision': DECISION,
    'continuous_source': {'type': 'boolean'}, 'no_off_camera_repair': {'type': 'boolean'},
    'no_fixture_replacement': {'type': 'boolean'}, 'readable_text': {'type': 'boolean'},
    'narration_or_captions_explain_transitions': {'type': 'boolean'}, 'no_exposed_credentials': {'type': 'boolean'},
    'answers': object_schema({key: {'type': 'string', 'minLength': 1, 'maxLength': 8000}
                             for key in ('what_deployed', 'codex_contribution', 'fault_cause',
                                         'delivered_patch', 'recovery_and_isolation', 'how_to_rerun')}),
    'confusing_steps': array({'type': 'string', 'minLength': 1, 'maxLength': 2000}, maximum=100),
    'unresolved_material_gaps': array({'type': 'string', 'minLength': 1, 'maxLength': 2000}, maximum=100),
    'optional_edits': array(object_schema({'video': REFERENCE, 'duration_seconds': SECONDS,
        'discloses_cuts_speed_and_elapsed_time': {'type': 'boolean'},
        'segments': array(object_schema({'source_range': RANGE, 'edit_range': RANGE,
                                        'speed': {'type': 'number', 'exclusiveMinimum': 0, 'maximum': 100}}),
                          minimum=1, maximum=10000)}), maximum=20),
})

QUALIFICATION_SCHEMA = document('qualification', {
    'schema_version': {'const': 1}, 'kind': {'const': 'factory-release-qualification'},
    'qualification_id': LABEL, 'created_at': UTC, 'author_id': LABEL,
    'package_manifest_sha256': DIGEST, 'scenario_sha256': DIGEST, 'assertions_sha256': DIGEST,
    'lock_sha256': DIGEST,
    'runs': array(object_schema({
        'role': {'enum': ['fresh-1', 'fresh-2', 'reset-replay']},
        'result': REFERENCE, 'operator_id': LABEL, 'operator_attested_at': UTC,
        'operator_performed_run': {'type': 'boolean'},
        'environment_origin': {'enum': ['fresh', 'retained', 'unknown']},
        'cluster_uid': {'type': ['string', 'null'], 'maxLength': 256},
        'environment_evidence': array(RELATIVE, maximum=32),
    }), minimum=3, maximum=3),
    'video_review': REFERENCE,
    # Retain independent gate receipts without promoting unimplemented adapters.
    'additional_gate_evidence': array(object_schema({'gate': {'enum': ['G' + str(i) for i in range(1, 11)]},
                                                    'artifacts': array(REFERENCE, minimum=1, maximum=100)}),
                                      maximum=10),
})

SCHEMAS = {'qualification': QUALIFICATION_SCHEMA, 'video-review': VIDEO_REVIEW_SCHEMA,
           'video-chapters': CHAPTERS_SCHEMA}
