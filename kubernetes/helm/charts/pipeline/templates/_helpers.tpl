{{- define "kernelci-pipeline.labels" -}}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: kernelci
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | quote }}
{{- end }}
{{- define "kernelci-pipeline.configMapName" -}}
{{- default "pipeline-configmap" .Values.pipelineConfig.existingConfigMap -}}
{{- end }}

