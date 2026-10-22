import { useQueryClient } from '@tanstack/react-query'

import { $api, messageOf } from '../../api/query'
import { Switch } from '../../components/Fields'
import { LoadFailed, Loading } from '../../components/States'
import { Callout } from '../../components/ui'
import { emailOrder, emailWhenLabel, summaryHourLabel } from '../../lib/notifications'
import { Row, SettingsCard } from './rows'

const KEY = ['get', '/api/core/notifications/preferences'] as const

const numberSetting = (settings: Array<{ key: string; value: unknown }>, key: string) => {
  const value = settings.find((s) => s.key === key)?.value
  return typeof value === 'number' ? value : undefined
}

/**
 * Settings → Notifications (201): which notifications also go out by email, and the daily
 * summary, each a switch saved as it is flipped. The deadline line and the summary line read
 * the organisation's own windows (how far ahead, at what hour). Staff only.
 */
export function Notifications() {
  const client = useQueryClient()
  const preferences = $api.useQuery('get', '/api/core/notifications/preferences')
  const settings = $api.useQuery('get', '/api/core/settings')
  const save = $api.useMutation('put', '/api/core/notifications/preferences', {
    onSuccess: (saved) => client.setQueriesData({ queryKey: KEY }, saved),
  })
  if (preferences.isPending) return <Loading what="Loading your preferences" />
  if (preferences.isError)
    return (
      <LoadFailed
        what="your preferences"
        error={preferences.error}
        onRetry={() => void preferences.refetch()}
      />
    )
  const { email, daily_summary: daily, kinds } = preferences.data
  const warning = numberSetting(settings.data ?? [], 'notifications.deadline_warning_days')
  const hour = numberSetting(settings.data ?? [], 'notifications.daily_summary_hour')
  return (
    <>
      {save.isError ? <Callout tone="danger">{messageOf(save.error)}</Callout> : null}
      <SettingsCard title="Email me when">
        {emailOrder(kinds).map((kind) => {
          const label = emailWhenLabel(kind, warning)
          return (
            <Row key={kind} label={label}>
              <Switch
                label={label}
                checked={email[kind] ?? false}
                disabled={save.isPending}
                onChange={(checked) => save.mutate({ body: { email: { [kind]: checked } } })}
              />
            </Row>
          )
        })}
      </SettingsCard>
      <SettingsCard title="Summary">
        <Row
          label={
            hour === undefined ? 'Daily summary' : `Daily summary at ${summaryHourLabel(hour)}`
          }
        >
          <Switch
            label="Daily summary"
            checked={daily}
            disabled={save.isPending}
            onChange={(checked) => save.mutate({ body: { daily_summary: checked } })}
          />
        </Row>
      </SettingsCard>
    </>
  )
}
