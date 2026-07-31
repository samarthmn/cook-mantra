import type { ReactNode } from "react";

interface ScreenHeaderProps {
  kicker: string;
  title: ReactNode;
  intro?: ReactNode;
  titleClassName?: string;
  showRule?: boolean;
}

export function ScreenHeader({
  kicker,
  title,
  intro,
  titleClassName = "",
  showRule = true,
}: ScreenHeaderProps) {
  return (
    <>
      <header className="screen-header">
        <p className="kicker">{kicker}</p>
        <h1 className={`screen-title ${titleClassName}`.trim()} tabIndex={-1}>
          {title}
        </h1>
        {intro ? <p className="screen-intro">{intro}</p> : null}
      </header>
      {showRule ? <hr className="section-rule" /> : null}
    </>
  );
}
