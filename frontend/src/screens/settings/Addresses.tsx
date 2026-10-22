import { useState } from 'react'

import { $api } from '../../api/query'
import { Dialog } from '../../components/Dialog'
import { LoadFailed, Loading } from '../../components/States'
import { Button, cx } from '../../components/ui'
import { Row, RowAction, SettingsCard } from './rows'

/** Postmark's inbound host: mail to the domain is handed to it, and it posts each message to the
 *  portal's webhook (DECISIONS 14: the provider is Postmark). */
const INBOUND_HOST = 'inbound.postmarkapp.com'
/** Postmark's sending servers, for the SPF record of a domain mail is also sent from (each
 *  request's alias sends its letters). */
const SPF = 'v=spf1 include:spf.mtasv.net ~all'

const RECORDS = (domain: string) => [
  { type: 'MX', host: domain, value: INBOUND_HOST, note: 'Priority 10' },
  { type: 'TXT', host: domain, value: SPF, note: 'For sending' },
]

/**
 * Settings → Request addresses (204), administrators: the domain every request's address lives
 * at, the form those addresses take, and what happens to mail for an address no request has.
 * The domain is the server's (`INBOUND_EMAIL_DOMAIN`) and has no Edit: changing it would stop
 * every address already given to an agency. "Set up" (223) shows the record the domain needs.
 */
export function Addresses() {
  const system = $api.useQuery('get', '/api/core/system')
  const [setUp, setSetUp] = useState(false)
  if (system.isPending) return <Loading what="Loading the addresses" />
  if (system.isError)
    return (
      <LoadFailed what="the addresses" error={system.error} onRetry={() => void system.refetch()} />
    )
  const domain = system.data.inbound_email_domain
  return (
    <>
      <SettingsCard title="Request addresses">
        <Row
          label="Domain"
          value={domain}
          action={<RowAction onClick={() => setSetUp(true)}>Set up</RowAction>}
        />
        <Row
          label="Each request gets"
          hint="A new random address"
          value={`xxxx-xxxx-xxxx@${domain}`}
          action={null}
        />
        <Row label="Mail to an unknown address" value="Goes to Flagged" action={null} />
      </SettingsCard>
      {setUp ? <SetUpDomain domain={domain} onClose={() => setSetUp(false)} /> : null}
    </>
  )
}

/** Setting up the request address (223): the DNS records the domain needs. The portal does not
 *  look them up, so no "Found" is claimed and there is no "Check again". */
function SetUpDomain({ domain, onClose }: { domain: string; onClose: () => void }) {
  return (
    <Dialog
      title={`Set up ${domain}`}
      open
      onClose={onClose}
      width={620}
      top={70}
      footer={
        <Button kind="text" pill onClick={onClose}>
          Close
        </Button>
      }
    >
      <div className="overflow-hidden rounded-12 bg-card-white shadow-field">
        {RECORDS(domain).map((record, index) => (
          <div
            key={record.type}
            className={cx(
              'grid grid-cols-[50px_170px_1fr_90px] items-center gap-x-3 px-3.5 py-2.5 text-125 leading-[normal]',
              index > 0 && 'border-t border-line-row-2',
            )}
          >
            <span className="text-ink-2">{record.type}</span>
            <span className="truncate text-ink">{record.host}</span>
            <span className="truncate text-ink-2" title={record.value}>
              {record.value}
            </span>
            <span className="text-right text-muted">{record.note}</span>
          </div>
        ))}
      </div>
      <p className="text-12 leading-body text-muted">
        Add these where the domain&rsquo;s DNS is kept: Postmark&rsquo;s inbound server takes the
        mail and hands each message to the portal. The DKIM and Return-Path records come from the
        Postmark account. The portal does not look the records up.
      </p>
    </Dialog>
  )
}
