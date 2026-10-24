// A person's round badge with their photo, or else their initials (0.1.7).
// The photo goes only where the name is written next to it: the account at
// the bottom of the sidebar, its menu, Account, a note's editor. Where the
// badge alone has to say whose something is (terminals in the sidebar and
// on a project's page), the initials stay: they say it at a glance, a
// picture does not (the owner, 24.10).

import { useState } from "react";

interface Props {
  initials: string;
  photo?: string | null;
  /** Its size and colour: mine, big, huge. */
  className?: string;
}

export function Face(props: Props) {
  // A photo that does not load (gone meanwhile, no connection) gives way to
  // the initials.
  const [broken, setBroken] = useState<string | null>(null);
  const extra = props.className ? ` ${props.className}` : "";
  if (props.photo && broken !== props.photo) {
    return (
      <img className={`avatar photo${extra}`} src={props.photo} alt=""
        aria-hidden="true" draggable={false}
        onError={() => setBroken(props.photo ?? null)} />
    );
  }
  return <span className={`avatar${extra}`} aria-hidden="true">{props.initials}</span>;
}
