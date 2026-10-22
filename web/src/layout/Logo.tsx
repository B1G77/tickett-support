// Two files, one per theme (the user's black wordmark for light, tickett.com's white one for dark);
// theme.css shows the right one, so it follows the device setting with no JavaScript.
export default function Logo({ className = "" }: { className?: string }) {
  return (
    <span className={`logo ${className}`}>
      <img className="logo-on-dark" src="/logo-white.png" alt="Tickett" />
      <img className="logo-on-light" src="/logo-black.png" alt="Tickett" />
    </span>
  );
}
