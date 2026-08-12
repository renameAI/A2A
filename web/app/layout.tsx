import "./globals.css";

export const metadata = {
  title: "rename. — Lead 발굴 워크스페이스",
  description: "기업 자료로 프로필을 만들고 보완성 기준으로 리드를 발굴합니다",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ko">
      <body>{children}</body>
    </html>
  );
}
