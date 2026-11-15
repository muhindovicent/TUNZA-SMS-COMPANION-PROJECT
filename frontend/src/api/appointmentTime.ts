const zone = 'Africa/Kampala'
const partsFormatter = new Intl.DateTimeFormat('en-GB', {
  timeZone: zone, year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
})

function parts(date: Date) {
  const values = Object.fromEntries(partsFormatter.formatToParts(date).map((part) => [part.type, part.value]))
  return `${values.year}-${values.month}-${values.day}T${values.hour}:${values.minute}:${values.second}`
}

export function kampalaToUtc(input: string): string {
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(input)) throw new Error('Enter a valid Kampala date and time.')
  const naive = new Date(`${input}:00Z`)
  if (!Number.isFinite(naive.getTime())) throw new Error('Invalid date and time.')
  const offset = new Date(`${parts(naive)}Z`).getTime() - naive.getTime()
  const utc = new Date(naive.getTime() - offset)
  if (parts(utc) !== `${input}:00`) throw new Error('Invalid Kampala date and time.')
  return utc.toISOString()
}

export function displayKampala(utc: string): string {
  return `${partsFormatter.format(new Date(utc))} (Africa/Kampala)`
}
