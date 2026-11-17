export function watchDesktopAccount(auth, onChange) {
  let current = null
  const { data } = auth.onAuthStateChange((_event, session) => {
    const id = typeof session?.user?.id === 'string' ? session.user.id : null
    if (id !== current) {
      current = id
      onChange(id)
    }
  })
  return () => data.subscription.unsubscribe()
}
