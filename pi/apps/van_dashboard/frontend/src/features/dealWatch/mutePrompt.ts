export function promptForMuteDays(
  displayTitle: string,
  showError: (message: string, tone: 'error') => void,
): number | null {
  const answer = window.prompt(`Mute notifications for ${displayTitle} for how many days?`, '7');
  if (answer === null) return null;
  const days = Number(answer.trim());
  if (!Number.isSafeInteger(days) || days < 1) {
    showError('Enter a whole number of days greater than zero', 'error');
    return null;
  }
  return days;
}
