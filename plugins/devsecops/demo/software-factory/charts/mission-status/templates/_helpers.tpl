{{- define "mission.selectorLabels" -}}
{{- if gt (len .Release.Name) 50 -}}{{ fail "mission-status release name must be at most 50 characters" }}{{- end -}}
app.kubernetes.io/name: mission-status
app.kubernetes.io/instance: {{ .Release.Name | quote }}
{{- end -}}
{{- define "mission.labels" -}}
{{ include "mission.selectorLabels" . }}
app.kubernetes.io/part-of: devsecops-software-factory
devsecops-demo-environment: {{ .Values.environment | quote }}
{{- end -}}
