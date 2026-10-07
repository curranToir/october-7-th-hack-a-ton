import Image from "next/image";

export function Logo({
  variant = "mark",
  size = 32,
  alt = "",
}: {
  variant?: "mark" | "wordmark";
  size?: number;
  alt?: string;
}) {
  const wordmark = variant === "wordmark";
  return (
    <Image
      className="toir-logo"
      src={wordmark ? "/brand/toir-logo.png" : "/brand/toir-mark.png"}
      alt={alt}
      width={wordmark ? 516 : 120}
      height={wordmark ? 308 : 120}
      style={{ width: size, height: "auto" }}
      unoptimized
      loading="eager"
    />
  );
}
