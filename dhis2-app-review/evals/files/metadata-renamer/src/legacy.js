// Old CSV export, replaced by the Import/Export app. Kept just in case.
export function toCsv(rows) {
    const header = 'id,name\n'
    return header + rows.map((r) => `${r.id},"${r.name}"`).join('\n')
}

// function downloadCsv(rows) {
//     const blob = new Blob([toCsv(rows)], { type: 'text/csv' })
//     const a = document.createElement('a')
//     a.href = URL.createObjectURL(blob)
//     a.download = 'dataElements.csv'
//     a.click()
// }
