{{- define "kernelci-api.labels" -}}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: kernelci
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | quote }}
{{- end }}

{{- define "kernelci-api.mongoUri" -}}
{{- $mongo := .Values.mongo | default dict -}}
{{- if $mongo.enabled -}}
mongodb://mongo:27017
{{- else -}}
{{- required "config.mongoService is required when mongo.enabled is false" .Values.config.mongoService -}}
{{- end -}}
{{- end }}

