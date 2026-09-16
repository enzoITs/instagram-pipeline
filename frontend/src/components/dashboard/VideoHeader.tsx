import { useState } from "react";

import type { VideoInfo } from "../../types";
import { formatDate } from "../../utils/format";
import { ExternalIcon } from "../Icons";

export default function VideoHeader({ video }: { video: VideoInfo }) {
  const [thumbnailFailed, setThumbnailFailed] = useState(false);
  const kind = video.media_type === "post" ? "Post" : "Reel";
  const facts = [
    video.published_at ? `publicado em ${formatDate(video.published_at)}` : null,
    video.duration_sec ? `${Math.round(video.duration_sec)} s de duração` : null,
  ].filter(Boolean);

  return (
    <div className="video">
      <div className="video__frame">
        {video.thumbnail_url && !thumbnailFailed ? (
          <img
            src={video.thumbnail_url}
            alt={`Capa do ${kind.toLowerCase()} ${video.instagram_id}`}
            loading="lazy"
            referrerPolicy="no-referrer"
            onError={() => setThumbnailFailed(true)}
          />
        ) : (
          <span className="video__placeholder" aria-hidden="true">
            {kind}
          </span>
        )}
      </div>
      <div className="video__meta">
        <p className="video__creator">{video.creator_username ?? "Criador não identificado"}</p>
        <p className="video__facts">
          {kind} {video.instagram_id}
          {facts.length > 0 && `, ${facts.join(", ")}`}
        </p>
        {video.caption && <p className="video__caption">{video.caption}</p>}
        <a className="video__link" href={video.url} target="_blank" rel="noopener noreferrer">
          Abrir no Instagram
          <ExternalIcon size={15} />
        </a>
      </div>
    </div>
  );
}
