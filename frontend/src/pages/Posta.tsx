import { Sheet } from '../components/Paper'
import { PageHeader } from '../components/PageHeader'

// Поща — stage 1 of #97: a signposted way into the shop's mailbox, nothing more.
//
// It LINKS OUT rather than embedding. Roundcube ships X-Frame-Options/frame-ancestors, so an <iframe>
// would render a blank pane with no visible error — the failure would look like a broken panel. Relaxing
// that header to embed it would weaken clickjacking protection on a mailbox full of customer
// correspondence, which is a poor trade for a cosmetic gain.
//
// Stage 2 (owner-approved, not built) is the part worth building: order context beside each message,
// which Roundcube can never do. It will read message HEADERS ONLY, pulled from the mail box over a
// narrow endpoint — so the panel never holds IMAP credentials and never holds message bodies. A panel
// compromise then exposes senders and subjects, not customer correspondence.
const MAIL_URL = 'https://mail.darlinghome.bg'

export function Posta() {
  return (
    <div style={{ display: 'grid', gap: '1.1rem' }}>
      <PageHeader title="Поща" subtitle="пощенската кутия на магазина — orders@darlinghome.bg" />

      <Sheet clip="left" tilt="l">
        <h3 style={{ fontSize: '1rem', marginBottom: '.4rem' }}>Отвори пощата</h3>
        <p style={{ fontSize: '.85rem', color: 'var(--pencil)', margin: '0 0 .8rem' }}>
          Пощата се отваря в нов раздел, със собствено влизане. Още не е вградена в панела — засега
          това е пряка връзка.
        </p>
        <a className="btn-paper" href={MAIL_URL} target="_blank" rel="noopener noreferrer"
          style={{ display: 'inline-block', textDecoration: 'none' }}>
          Отвори {MAIL_URL.replace('https://', '')} ↗
        </a>
      </Sheet>

      <Sheet>
        <h3 style={{ fontSize: '.95rem', marginBottom: '.4rem' }}>Какво предстои</h3>
        <p style={{ fontSize: '.82rem', color: 'var(--pencil)', margin: 0 }}>
          Следващата стъпка е списък с писмата <strong>до поръчките на същия клиент</strong> — това,
          което обикновената поща не може да покаже. Панелът ще чете само подател, тема и дата;
          самите писма остават в пощенската кутия и се четат там. Така достъпът до панела не дава
          достъп до кореспонденцията с клиентите.
        </p>
      </Sheet>
    </div>
  )
}
